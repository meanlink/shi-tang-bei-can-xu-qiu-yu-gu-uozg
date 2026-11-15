"""保留期限、保全与到期清理。

正确性要点：
- 清理按 run + item 记录进度，中断后从断点续跑；同一投递在同一 run 内只处理一次。
- 逐项独立事务，行锁复核保全/到期，重试不重复释放引用。
- 缺物理文件的投递标记 failed，不冒充清理完成。
- 释放引用只把 blob 置为孤儿；物理回收由 storage.reap_orphans 在另一阶段完成。
"""
import time, logging
from sqlalchemy import select, func, and_
from fastapi import HTTPException
from src.models import Delivery, ContentBlob, CleanupRun, CleanupItem
from src.services import storage

BATCH_LIMIT = 200

def due_filter(now: float):
    return and_(Delivery.state == 'active', Delivery.held == False,
                Delivery.expires_at.is_not(None), Delivery.expires_at <= now)

def preview(db) -> dict:
    """页面预览：待清理、保全、缺文件、可回收空间等。"""
    now = time.time()
    due_q = select(Delivery).where(due_filter(now))
    due_rows = list(db.scalars(due_q))
    held_total = db.scalar(select(func.count()).select_from(Delivery).where(Delivery.held == True))
    failed_total = db.scalar(select(func.count()).select_from(Delivery).where(Delivery.state == 'failed'))
    cleaned_total = db.scalar(select(func.count()).select_from(Delivery).where(Delivery.state == 'cleaned'))
    active_total = db.scalar(select(func.count()).select_from(Delivery).where(Delivery.state == 'active'))

    # 逻辑可回收：到期投递引用的 blob，仅当其全部活跃引用都在本批到期、释放后归零才计入
    due_blob_ids = {d.blob_id for d in due_rows if d.blob_id}
    logical_bytes = 0
    if due_blob_ids:
        # 每个 blob 仍“未到期且在档”的引用数（这些引用不会被本次清理释放）
        retained_rows = db.execute(
            select(Delivery.blob_id, func.count())
            .where(Delivery.state == 'active',
                   Delivery.blob_id.in_(due_blob_ids),
                   (Delivery.held == True) |
                   (Delivery.expires_at.is_(None)) | (Delivery.expires_at > now))
            .group_by(Delivery.blob_id)).all()
        retained_map = {blob_id: cnt for blob_id, cnt in retained_rows}
        for blob in db.scalars(select(ContentBlob).where(ContentBlob.id.in_(due_blob_ids))):
            if retained_map.get(blob.id, 0) == 0:
                logical_bytes += blob.size_bytes

    # 已在等待物理回收的孤儿 blob 空间
    orphan_bytes = db.scalar(select(func.coalesce(func.sum(ContentBlob.size_bytes), 0)).where(
        ContentBlob.orphan == True, ContentBlob.reaped == False)) or 0

    # 缺文件的投递（active 与 failed 都要能追查；cleaned 已无文件属正常）
    missing = []
    for d in db.scalars(select(Delivery).where(
            Delivery.state.in_(('active', 'failed'))).limit(1000)):
        blob = d.blob_id and db.get(ContentBlob, d.blob_id)
        if blob is None or not storage.files_exist(blob):
            missing.append(d.id)
    return {
        'at': now,
        'active_total': active_total,
        'due_total': len(due_rows),
        'held_total': held_total,
        'failed_total': failed_total,
        'cleaned_total': cleaned_total,
        'logical_reclaimable_bytes': logical_bytes,
        'orphan_bytes': orphan_bytes,
        'reclaimable_bytes': logical_bytes + orphan_bytes,
        'missing_delivery_ids': missing,
        'due_delivery_ids': [d.id for d in due_rows],
    }

def _resume_or_create_run(db, actor: str) -> CleanupRun:
    running = db.scalar(select(CleanupRun).where(CleanupRun.status == 'running').order_by(CleanupRun.id.desc()))
    if running:
        return running
    run = CleanupRun(triggered_by=actor, started_at=time.time(), status='running', last_delivery_id=0)
    db.add(run); db.flush()
    return run

def _process_delivery(db, run: CleanupRun, delivery_id: int) -> tuple[str, int, str | None]:
    """处理单个投递，返回 (result, reclaimed_bytes, detail)。"""
    now = time.time()
    d = db.get(Delivery, delivery_id, with_for_update=True)
    if d is None:
        return 'skipped', 0, '投递不存在'
    # 幂等：已在本批次处理过（正常续跑不应进入，双保险）
    exists = db.scalar(select(CleanupItem.id).where(
        CleanupItem.run_id == run.id, CleanupItem.delivery_id == d.id))
    if exists:
        return 'skipped', 0, '本批次已处理'
    if d.state == 'cleaned':
        return 'skipped', 0, '引用此前已释放'
    if d.held:
        return 'held', 0, '已保全，跳过'
    if d.expires_at is None or d.expires_at > now:
        return 'skipped', 0, '尚未到期'
    blob = d.blob_id and db.get(ContentBlob, d.blob_id, with_for_update=True)
    if blob is None or not storage.files_exist(blob):
        d.state = 'failed'
        d.last_error = '归档物理文件缺失，未释放引用，请人工追查'
        detail = d.last_error
        result = 'missing'
        reclaimed = 0
    else:
        reclaimed = storage.release_reference(db, blob.id)
        d.state = 'cleaned'
        d.cleaned_at = now
        d.cleanup_run_id = run.id
        d.last_error = None
        result = 'cleaned'
        detail = f'释放 blob {blob.checksum[:16]}…'
    db.add(CleanupItem(run_id=run.id, delivery_id=d.id, result=result,
                       detail=detail, reclaimed_bytes=reclaimed, processed_at=now))
    return result, reclaimed, detail

def run_cleanup(db, actor: str, max_candidates: int = 1000) -> CleanupRun:
    """执行（或续跑）一次清理。调用方负责外层互斥锁与提交。"""
    run = _resume_or_create_run(db, actor)
    processed = 0
    while processed < max_candidates:
        now = time.time()
        batch = list(db.scalars(
            select(Delivery.id).where(due_filter(now))
            .order_by(Delivery.id).limit(BATCH_LIMIT)))
        if not batch:
            break
        for delivery_id in batch:
            try:
                result, reclaimed, _detail = _process_delivery(db, run, delivery_id)
                run.last_delivery_id = max(run.last_delivery_id, delivery_id)
                run.total_candidates += 1
                run.reclaimed_bytes += reclaimed
                if result == 'cleaned':
                    run.cleaned_count += 1
                elif result == 'held':
                    run.held_count += 1
                elif result == 'missing':
                    run.missing_count += 1
                elif result == 'failed':
                    run.failed_count += 1
                else:
                    run.skipped_count += 1
                processed += 1
                if processed >= max_candidates:
                    break
            except Exception as e:
                logging.exception('清理投递 %s 失败', delivery_id)
                run.failed_count += 1
                run.total_candidates += 1
                db.add(CleanupItem(run_id=run.id, delivery_id=delivery_id, result='failed',
                                   detail=str(e)[:480], processed_at=time.time()))
            db.commit()  # 逐项提交：中断后已处理项不回滚、不重复
        if len(batch) < BATCH_LIMIT:
            break
    # 再无到期项即收尾；否则保留 running 供下次续跑。含缺文件失败也正常收尾，失败项可追查
    remaining = db.scalar(select(Delivery.id).where(due_filter(time.time())).limit(1))
    if remaining is None:
        run.status = 'completed'
        run.finished_at = time.time()
    db.commit()
    return run

def list_runs(db, limit: int = 50):
    return list(db.scalars(select(CleanupRun).order_by(CleanupRun.id.desc()).limit(limit)))

def run_detail(db, run_id: int):
    run = db.get(CleanupRun, run_id)
    if not run:
        raise HTTPException(404, '清理批次不存在')
    items = list(db.scalars(select(CleanupItem).where(CleanupItem.run_id == run_id)
                            .order_by(CleanupItem.id.desc()).limit(200)))
    return run, items
