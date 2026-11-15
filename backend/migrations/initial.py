"""数据库结构迁移与历史归档回填。

- create_all 建立新表（content_blobs / cleanup_runs / cleanup_items）。
- 对已存在的旧表用 ADD COLUMN IF NOT EXISTS 补齐新列（MySQL 8 与 SQLite 均支持）。
- 历史 deliveries 行回填 content_blobs 关联，并把物理文件迁移到内容寻址目录；
  同 checksum 的历史文件只保留一份，完成存量去重。
迁移幂等，可重复执行。
"""
import os, time, hashlib, shutil, logging
from sqlalchemy import text, select
from src.config.database import Base, engine, Session
from src import models
from src.config.settings import ARTIFACT_DIR
from src.services import storage

# 表 -> 需要补齐的列 (列名, 列DDL)
_ADDED_COLUMNS = {
    'reports': [('retention_days', 'INT NULL')],
    'deliveries': [
        ('blob_id', 'INT NULL'),
        ('retention_days', 'INT NULL'),
        ('expires_at', 'DOUBLE NULL'),
        ('held', 'BOOL DEFAULT 0'),
        ('held_at', 'DOUBLE NULL'),
        ('held_by', 'VARCHAR(150) NULL'),
        ('hold_reason', 'VARCHAR(300) NULL'),
        ('state', "VARCHAR(20) DEFAULT 'active'"),
        ('cleaned_at', 'DOUBLE NULL'),
        ('cleanup_run_id', 'INT NULL'),
        ('last_error', 'VARCHAR(500) NULL'),
        ('size_bytes', 'INT DEFAULT 0'),
    ],
}

def _existing_columns(conn, table: str) -> set:
    rows = conn.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name=:t"),
        {'t': table}) if engine.dialect.name != 'sqlite' else conn.execute(
        text("PRAGMA table_info(%s)" % table))
    if engine.dialect.name == 'sqlite':
        return {r[1] for r in rows}
    return {r[0] for r in rows}

def _add_missing_columns(conn):
    for table, columns in _ADDED_COLUMNS.items():
        try:
            present = _existing_columns(conn, table)
        except Exception:
            continue
        for name, ddl in columns:
            if name not in present:
                conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))
                logging.info('迁移：%s 增加列 %s', table, name)

def _backfill_legacy_deliveries():
    """把旧布局 report-*-delivery-*.csv(.json) 迁移进内容寻址存储并回填 blob。"""
    from src.models import Delivery, ContentBlob
    if not os.path.isdir(ARTIFACT_DIR):
        return
    legacy = [f for f in os.listdir(ARTIFACT_DIR)
              if f.startswith('report-') and f.endswith('.csv')]
    if not legacy:
        return
    with Session() as db:
        blob_cache = {}  # checksum -> ContentBlob
        for fname in legacy:
            csv_abs = os.path.join(ARTIFACT_DIR, fname)
            json_abs = csv_abs + '.json'
            delivery = db.scalar(select(Delivery).where(Delivery.filename == fname))
            if delivery is None or delivery.blob_id is not None:
                continue
            try:
                with open(csv_abs, 'rb') as f:
                    csv_bytes = f.read()
                checksum = delivery.checksum or hashlib.sha256(csv_bytes).hexdigest()
                json_bytes = b''
                if os.path.exists(json_abs):
                    with open(json_abs, 'rb') as f:
                        json_bytes = f.read()
                csv_rel, json_rel = storage.blob_paths(checksum)
                target_csv = storage.abspath(csv_rel)
                target_json = storage.abspath(json_rel)
                os.makedirs(os.path.dirname(target_csv), exist_ok=True)
                # 同内容多份历史文件：第一份移动，后续删除，实现存量去重
                if not os.path.exists(target_csv):
                    shutil.move(csv_abs, target_csv)
                else:
                    os.unlink(csv_abs)
                if json_bytes:
                    if not os.path.exists(target_json):
                        shutil.move(json_abs, target_json)
                    elif os.path.exists(json_abs):
                        os.unlink(json_abs)
                elif not os.path.exists(target_json):
                    # 缺 JSON 说明文件时按现有 CSV 补一个最小说明
                    import json as _json
                    payload = _json.dumps(
                        {'checksum': checksum, 'note': '历史归档迁移补齐说明文件'},
                        ensure_ascii=False).encode('utf-8')
                    with open(target_json, 'wb') as f:
                        f.write(payload)
                    json_bytes = payload
                blob = blob_cache.get(checksum) or db.scalar(
                    select(ContentBlob).where(ContentBlob.checksum == checksum)
                    .with_for_update())
                if blob is None:
                    now = time.time()
                    blob = ContentBlob(checksum=checksum, csv_path=csv_rel, json_path=json_rel,
                                       size_bytes=len(csv_bytes) + len(json_bytes),
                                       ref_count=0, orphan=False, reaped=False,
                                       created_at=now, last_referenced_at=now)
                    db.add(blob); db.flush()
                    blob_cache[checksum] = blob
                blob.ref_count += 1
                blob.orphan = False
                blob.last_referenced_at = time.time()
                delivery.blob_id = blob.id
                delivery.state = delivery.state or 'active'
                delivery.size_bytes = blob.size_bytes
            except Exception:
                logging.exception('迁移历史归档 %s 失败，保留原文件', fname)
        db.commit()

def migrate():
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        _add_missing_columns(conn)
    _backfill_legacy_deliveries()

if __name__ == '__main__':
    migrate()
    print('migration completed')
