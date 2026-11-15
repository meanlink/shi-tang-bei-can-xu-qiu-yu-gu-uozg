import os
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from src.config.database import get_db
from src.models import Report, Delivery, ContentBlob
from src.schemas.report import (ReportInput, HoldInput, DeliveryRetentionInput,
                                ReportRetentionInput)
from src.middlewares.auth import current_user, admin_only
from src.services import reports, retention, storage
from src.services.audit import record
from src.utils.locking import named_lock
from src.utils.serialization import (report_dict, delivery_dict, blob_dict,
                                     cleanup_run_dict, cleanup_item_dict)
router = APIRouter()

CLEANUP_LOCK = 'archive:cleanup-lock'
REAP_LOCK = 'archive:reap-lock'

@router.get('/reports')
def listing(db=Depends(get_db), u=Depends(current_user)):
    return [report_dict(r) for r in db.scalars(select(Report).order_by(Report.id.desc()))]

@router.post('/reports', status_code=201)
def create(data: ReportInput, db=Depends(get_db), u=Depends(admin_only)):
    r = reports.create(db, data)
    record(db, u.email, 'report.schedule', {'id': r.id, 'destination': r.destination,
                                            'retention_days': r.retention_days})
    db.commit()
    return report_dict(r)

@router.post('/reports/{id}/toggle')
def toggle(id: int, db=Depends(get_db), u=Depends(admin_only)):
    r = db.get(Report, id)
    if not r:
        raise HTTPException(404, '分发计划不存在')
    r.enabled = not r.enabled
    record(db, u.email, 'report.toggle', {'id': id, 'enabled': r.enabled})
    db.commit()
    return report_dict(r)

@router.post('/reports/{id}/retention')
def report_retention(id: int, data: ReportRetentionInput, db=Depends(get_db),
                     u=Depends(admin_only)):
    r = db.get(Report, id)
    if not r:
        raise HTTPException(404, '分发计划不存在')
    reports.set_report_retention(db, r, data.retention_days)
    record(db, u.email, 'report.retention', {'id': id, 'retention_days': r.retention_days})
    db.commit()
    return report_dict(r)

@router.get('/deliveries')
def deliveries(state: str | None = None, held: bool | None = None,
               db=Depends(get_db), u=Depends(current_user)):
    stmt = select(Delivery).order_by(Delivery.id.desc()).limit(200)
    if state:
        if state not in ('active', 'cleaned', 'failed'):
            raise HTTPException(422, '状态仅支持 active / cleaned / failed')
        stmt = stmt.where(Delivery.state == state)
    if held is not None:
        stmt = stmt.where(Delivery.held == held)
    return [delivery_dict(d) for d in db.scalars(stmt)]

@router.get('/deliveries/{id}')
def delivery_detail(id: int, db=Depends(get_db), u=Depends(current_user)):
    d = db.get(Delivery, id)
    if not d:
        raise HTTPException(404, '归档不存在')
    out = delivery_dict(d)
    if d.blob_id:
        blob = db.get(ContentBlob, d.blob_id)
        out['blob'] = blob_dict(blob) if blob else None
    return out

@router.post('/deliveries/{id}/hold')
def hold(id: int, data: HoldInput, db=Depends(get_db), u=Depends(admin_only)):
    d = db.get(Delivery, id)
    if not d:
        raise HTTPException(404, '归档不存在')
    reports.set_hold(db, d, data.hold, u.email, data.reason)
    record(db, u.email, 'delivery.hold' if data.hold else 'delivery.unhold',
           {'id': id, 'reason': data.reason})
    db.commit()
    return delivery_dict(d)

@router.post('/deliveries/{id}/retention')
def delivery_retention(id: int, data: DeliveryRetentionInput, db=Depends(get_db),
                       u=Depends(admin_only)):
    d = db.get(Delivery, id)
    if not d:
        raise HTTPException(404, '归档不存在')
    reports.set_retention(db, d, data.retention_days, u.email)
    record(db, u.email, 'delivery.retention',
           {'id': id, 'retention_days': d.retention_days, 'expires_at': d.expires_at})
    db.commit()
    return delivery_dict(d)

@router.get('/deliveries/{id}/download')
def download(id: int, format: str = 'csv', db=Depends(get_db), u=Depends(current_user)):
    d = db.get(Delivery, id)
    if not d:
        raise HTTPException(404, '归档不存在')
    if format not in ('csv', 'json'):
        raise HTTPException(422, '支持 CSV 或 JSON')
    if d.state == 'cleaned':
        raise HTTPException(410, '该归档已过保留期并清理引用，文件不再提供下载；投递记录仍可追查')
    blob = d.blob_id and db.get(ContentBlob, d.blob_id)
    if blob is None:
        raise HTTPException(404, '归档内容索引缺失，请重新安排分发')
    rel = blob.json_path if format == 'json' else blob.csv_path
    p = storage.abspath(rel)
    if not os.path.exists(p):
        raise HTTPException(404, '归档文件缺失，已列入异常，请人工追查')
    download_name = d.filename.replace('.csv', '') + ('.json' if format == 'json' else '.csv')
    return FileResponse(p, filename=download_name,
                        media_type='application/json' if format == 'json' else 'text/csv; charset=utf-8')

@router.get('/retention/preview')
def retention_preview(db=Depends(get_db), u=Depends(current_user)):
    return retention.preview(db)

@router.get('/blobs')
def blobs(db=Depends(get_db), u=Depends(current_user)):
    rows = db.scalars(select(ContentBlob).order_by(ContentBlob.id.desc()).limit(200))
    return [blob_dict(b) for b in rows]

@router.post('/cleanup/run')
def trigger_cleanup(db=Depends(get_db), u=Depends(admin_only)):
    # 互斥：保全/清理并发、清理/新归档并发由行锁与引用计数保证，这里串行化批次执行
    try:
        with named_lock(CLEANUP_LOCK):
            run = retention.run_cleanup(db, u.email)
            record(db, u.email, 'cleanup.run', {'id': run.id, 'status': run.status,
                                                'cleaned': run.cleaned_count,
                                                'missing': run.missing_count})
            db.commit()
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return cleanup_run_dict(run)

@router.post('/cleanup/reap')
def trigger_reap(db=Depends(get_db), u=Depends(admin_only)):
    try:
        with named_lock(REAP_LOCK):
            stats = storage.reap_orphans(db)
            record(db, u.email, 'cleanup.reap', stats)
            db.commit()
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return stats

@router.get('/cleanup/runs')
def cleanup_runs(db=Depends(get_db), u=Depends(current_user)):
    return [cleanup_run_dict(r) for r in retention.list_runs(db)]

@router.get('/cleanup/runs/{id}')
def cleanup_run(id: int, db=Depends(get_db), u=Depends(current_user)):
    run, items = retention.run_detail(db, id)
    return {'run': cleanup_run_dict(run), 'items': [cleanup_item_dict(i) for i in items]}
