import time, csv, io, hashlib, json
from fastapi import HTTPException
from src.models import Job, Report, Delivery
from src.services import storage

RETENTION_MIN_DAYS = 1
RETENTION_MAX_DAYS = 3650

def create(db, data):
    j = db.get(Job, data.job_id)
    if not j or j.status != 'completed' or not j.approved:
        raise HTTPException(409, '请先选择已完成且获批准的分析结果')
    retention = getattr(data, 'retention_days', None)
    _validate_retention(retention)
    r = Report(name=data.name, job_id=j.id, destination=data.destination,
               interval_seconds=data.interval_seconds,
               next_run=time.time() + data.delay_seconds, enabled=True,
               retention_days=retention)
    db.add(r); db.flush()
    return r

def _validate_retention(days):
    if days is None:
        return
    if not isinstance(days, int) or not (RETENTION_MIN_DAYS <= days <= RETENTION_MAX_DAYS):
        raise HTTPException(422, f'保留天数需在 {RETENTION_MIN_DAYS}~{RETENTION_MAX_DAYS} 之间，留空表示长期保留')

def _render(job):
    out = io.StringIO()
    out.write('﻿')
    table = job.result.get('table', {})
    cols = table.get('columns', [])
    writer = csv.writer(out)
    def safe(v):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return v
        text = str(v)
        return "'" + text if text.startswith(('=', '+', '-', '@', '\t', '\r')) else text
    writer.writerow([safe(c['label']) for c in cols])
    for row in table.get('rows', []):
        writer.writerow([safe(row.get(c['key'], '')) for c in cols])
    csv_bytes = out.getvalue().encode('utf-8')
    checksum = hashlib.sha256(csv_bytes).hexdigest()
    json_bytes = json.dumps(
        {'job': job.name, 'params': job.params, 'result': job.result, 'checksum': checksum},
        ensure_ascii=False, allow_nan=False).encode('utf-8')
    return csv_bytes, json_bytes

def deliver(db, report):
    """生成一次投递；同内容复用 blob，投递记录彼此独立、各自可查。"""
    j = db.get(Job, report.job_id)
    if not j or not j.approved:
        return None
    csv_bytes, json_bytes = _render(j)
    checksum = hashlib.sha256(csv_bytes).hexdigest()
    now = time.time()
    blob = storage.ensure_blob(db, checksum, csv_bytes, json_bytes)
    retention_days = report.retention_days
    expires_at = now + retention_days * 86400 if retention_days else None
    delivery = Delivery(
        report_id=report.id, created_at=now, checksum=checksum, filename='',
        destination=report.destination, blob_id=blob.id,
        retention_days=retention_days, expires_at=expires_at,
        held=False, state='active', size_bytes=blob.size_bytes)
    db.add(delivery); db.flush()
    # 展示名仍按投递唯一，物理文件改为内容寻址共享
    delivery.filename = f'report-{report.id}-delivery-{delivery.id}.csv'
    report.next_run = now + report.interval_seconds
    return delivery

def set_hold(db, delivery: Delivery, hold: bool, actor: str, reason: str | None):
    if delivery.state != 'active':
        raise HTTPException(409, '仅在档投递可以设置或解除保全')
    delivery.held = hold
    if hold:
        delivery.held_at = time.time()
        delivery.held_by = actor
        delivery.hold_reason = (reason or '负责人保全')[:300]
    else:
        delivery.held_at = None
        delivery.held_by = None
        delivery.hold_reason = None
    return delivery

def set_retention(db, delivery: Delivery, retention_days, actor: str):
    _validate_retention(retention_days)
    if delivery.state != 'active':
        raise HTTPException(409, '仅在档投递可以调整保留期限')
    delivery.retention_days = retention_days
    delivery.expires_at = delivery.created_at + retention_days * 86400 if retention_days else None
    return delivery

def set_report_retention(db, report: Report, retention_days):
    _validate_retention(retention_days)
    report.retention_days = retention_days
    return report
