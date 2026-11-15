import time, json, logging
from pathlib import Path
from sqlalchemy import select
from redis import Redis
from src.config.database import Session
from src.config.settings import REDIS_URL
from src.models import Job, Dataset, Report, Delivery, ContentBlob, CleanupRun
from src.services.business import analyze
from src.services.reports import deliver
from src.services import retention, storage
from src.services.audit import record
from src.utils.locking import named_lock
logging.basicConfig(level=logging.INFO)

CLEANUP_INTERVAL = 30        # 每 N 个 tick 检查一次到期清理
REAP_INTERVAL = 15           # 物理回收更频繁，尽快释放磁盘
_tick_count = 0

def execute(job_id):
    with Session() as db:
        j = db.get(Job, job_id)
        if not j or j.status != 'queued':
            return
        j.status = 'running'; db.commit()
        try:
            d = db.get(Dataset, j.dataset_id)
            result = analyze(d.records, j.params)
            json.dumps(result, allow_nan=False)
            j.result = result; j.status = 'completed'; j.finished_at = time.time()
            record(db, 'worker', 'job.completed', {'id': j.id})
        except Exception as e:
            logging.exception('Analysis failed')
            j.status = 'failed'; j.error = str(e)[:990]; j.finished_at = time.time()
            record(db, 'worker', 'job.failed', {'id': j.id, 'error': j.error})
        db.commit()

def _due_exists(db) -> bool:
    return db.scalar(select(Delivery.id).where(
        retention.due_filter(time.time())).limit(1)) is not None

def _scheduled_cleanup(db):
    """到期自动清理：有续跑中的批次则续跑，否则存在到期投递才开新批次。"""
    running = db.scalar(select(CleanupRun).where(CleanupRun.status == 'running'))
    if running is None and not _due_exists(db):
        return
    try:
        with named_lock('archive:cleanup-lock', ttl_seconds=900):
            run = retention.run_cleanup(db, 'worker-scheduler')
            record(db, 'worker', 'cleanup.auto',
                   {'id': run.id, 'status': run.status, 'cleaned': run.cleaned_count,
                    'missing': run.missing_count, 'reclaimed_bytes': run.reclaimed_bytes})
            db.commit()
    except RuntimeError:
        logging.info('清理锁被占用，本 tick 跳过')
    except Exception:
        logging.exception('定时清理失败，下个 tick 可续跑')

def _scheduled_reap(db):
    has_orphan = db.scalar(select(ContentBlob.id).where(
        ContentBlob.orphan == True, ContentBlob.reaped == False).limit(1))
    if has_orphan is None:
        return
    try:
        with named_lock('archive:reap-lock', ttl_seconds=600):
            stats = storage.reap_orphans(db)
            if stats['reaped'] or stats['failed']:
                record(db, 'worker', 'cleanup.reap.auto', stats)
            db.commit()
    except RuntimeError:
        logging.info('回收锁被占用，本 tick 跳过')
    except Exception:
        logging.exception('物理回收失败，下个 tick 重试')

def tick():
    global _tick_count
    _tick_count += 1
    with Session() as db:
        queued = list(db.scalars(select(Job.id).where(Job.status == 'queued').limit(3)))
    for job_id in queued:
        execute(job_id)
    with Session() as db:
        for r in db.scalars(select(Report).where(Report.enabled == True, Report.next_run <= time.time())):
            d = deliver(db, r)
            if d:
                record(db, 'worker', 'report.delivered', {'id': r.id, 'delivery': d.id})
        db.commit()
    # 清理与回收使用独立会话，避免长事务锁住投递表
    if _tick_count % CLEANUP_INTERVAL == 0:
        with Session() as db:
            _scheduled_cleanup(db)
    if _tick_count % REAP_INTERVAL == 0:
        with Session() as db:
            _scheduled_reap(db)

def main():
    r = Redis.from_url(REDIS_URL, socket_timeout=3)
    # This Compose stack has a single worker. A crash must not strand its jobs.
    with Session() as db:
        for j in db.scalars(select(Job).where(Job.status == 'running')):
            j.status = 'queued'; record(db, 'worker', 'job.recovered', {'id': j.id})
        db.commit()
    while True:
        Path('/tmp/heartbeat').touch()
        try:
            item = r.blpop('analysis:queue', timeout=1)
            if item:
                execute(int(item[1]))
            tick()
        except Exception:
            logging.exception('Worker retry'); time.sleep(2)
if __name__ == '__main__':
    main()
