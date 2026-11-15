"""内容寻址归档存储。

- 同 SHA256 内容在磁盘只保存一份（CSV + JSON 说明文件）。
- 数据库 content_blobs 以 ref_count 记录引用数；引用归零仅置 orphan，不立即删除。
- 物理回收在独立事务中锁定 orphan 行后删文件，避免与新归档竞争误删。
"""
import os, time, tempfile, logging
from sqlalchemy import select
from src.config.settings import ARTIFACT_DIR
from src.models import ContentBlob

CONTENT_SUBDIR = 'content'

def _abs(rel_path: str) -> str:
    return os.path.join(ARTIFACT_DIR, rel_path)

def abspath(rel_path: str) -> str:
    return _abs(rel_path)

def blob_paths(checksum: str):
    rel_dir = os.path.join(CONTENT_SUBDIR, checksum[:2])
    return (os.path.join(rel_dir, checksum + '.csv'),
            os.path.join(rel_dir, checksum + '.json'))

def _atomic_write(abs_path: str, payload: bytes):
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(abs_path), suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, abs_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def ensure_blob(db, checksum: str, csv_bytes: bytes, json_bytes: bytes) -> ContentBlob:
    """返回引用计数 +1 后的 blob；同内容复用，缺文件自愈，孤儿行重新激活。"""
    now = time.time()
    blob = db.scalar(select(ContentBlob).where(ContentBlob.checksum == checksum).with_for_update())
    csv_rel, json_rel = blob_paths(checksum)
    size = len(csv_bytes) + len(json_bytes)
    if blob is None:
        blob = ContentBlob(checksum=checksum, csv_path=csv_rel, json_path=json_rel,
                           size_bytes=size, ref_count=0, orphan=False, reaped=False,
                           created_at=now, last_referenced_at=now)
        db.add(blob)
        db.flush()
    # 活跃或孤儿行都可能缺物理文件（历史异常、外部清理），写入前自愈
    if blob.reaped or not os.path.exists(_abs(blob.csv_path)) or not os.path.exists(_abs(blob.json_path)):
        _atomic_write(_abs(csv_rel), csv_bytes)
        _atomic_write(_abs(json_rel), json_bytes)
        blob.csv_path, blob.json_path, blob.size_bytes = csv_rel, json_rel, size
        blob.reaped = False
        blob.reaped_at = None
    # 重新引用：孤儿回收标记撤销，杜绝“清理后新归档同内容被误删”
    blob.orphan = False
    blob.ref_count += 1
    blob.last_referenced_at = now
    db.flush()
    return blob

def release_reference(db, blob_id: int) -> int:
    """释放一个投递对 blob 的引用。返回本次真正可回收的字节数（引用归零时）。"""
    blob = db.get(ContentBlob, blob_id, with_for_update=True)
    if blob is None:
        return 0
    blob.ref_count = max(0, blob.ref_count - 1)
    blob.last_referenced_at = time.time()
    if blob.ref_count == 0 and not blob.orphan:
        blob.orphan = True
        return blob.size_bytes
    return 0

def physical_paths(blob: ContentBlob):
    return _abs(blob.csv_path), _abs(blob.json_path)

def files_exist(blob: ContentBlob) -> bool:
    csv_abs, json_abs = physical_paths(blob)
    return os.path.exists(csv_abs) and os.path.exists(json_abs)

def reap_orphans(db, limit: int = 50) -> dict:
    """物理回收孤儿 blob。独立事务逐行加锁后删文件，返回回收统计。"""
    now = time.time()
    orphans = list(db.scalars(
        select(ContentBlob)
        .where(ContentBlob.orphan == True, ContentBlob.reaped == False,
               ContentBlob.ref_count == 0)
        .order_by(ContentBlob.id).limit(limit).with_for_update(skip_locked=True)))
    reaped, bytes_freed, failed = 0, 0, 0
    for blob in orphans:
        # 二次确认：拿锁后仍为孤儿才删，新归档可能已把它重新激活
        db.refresh(blob, with_for_update=True)
        if blob.ref_count > 0 or not blob.orphan or blob.reaped:
            continue
        try:
            csv_abs, json_abs = physical_paths(blob)
            for p in (csv_abs, json_abs):
                if os.path.exists(p):
                    os.unlink(p)
            blob.reaped = True
            blob.reaped_at = now
            bytes_freed += blob.size_bytes
            reaped += 1
        except Exception:
            logging.exception('回收 blob %s 失败', blob.checksum)
            failed += 1
    return {'reaped': reaped, 'bytes_freed': bytes_freed, 'failed': failed,
            'remaining': db.scalar(select(ContentBlob.id).where(
                ContentBlob.orphan == True, ContentBlob.reaped == False).limit(1)) is not None}
