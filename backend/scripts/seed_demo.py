import time, json
from sqlalchemy import select, func
from src.config.database import Session
from migrations.initial import migrate
from src.models import User, Dataset, Job, Report, Delivery, ContentBlob, Audit
from src.services.business import metadata, seed_records, analyze
from src.services.reports import deliver
from src.services import storage
from src.utils.security import hash_password
from src.utils.serialization import checksum

def seed():
    migrate()
    with Session() as db:
        for role, name in [('admin', '林知 · 业务负责人'), ('analyst', '陈舟 · 分析员'),
                           ('viewer', '周禾 · 观察员')]:
            email = role + '@demo.local'
            if not db.scalar(select(User).where(User.email == email)):
                db.add(User(email=email, name=name, role=role,
                            password=hash_password('Demo123!')))
        db.flush()
        m = metadata()
        d = db.scalar(select(Dataset).where(Dataset.name == m['dataset_name']))
        if not d:
            rows = seed_records()
            d = Dataset(name=m['dataset_name'], source=m['short'] + '-202611.csv',
                        records=rows, checksum=checksum(rows), created_at=time.time())
            db.add(d); db.flush()
        if not db.scalar(select(Job).where(Job.name == '基线复核 · ' + m['name'])):
            params = {p['key']: p['default'] for p in m.get('parameters', [])}
            result = analyze(d.records, params)
            json.dumps(result, ensure_ascii=False, allow_nan=False)
            admin = db.scalar(select(User).where(User.role == 'admin'))
            j = Job(name='基线复核 · ' + m['name'], dataset_id=d.id, params=params,
                    status='completed', result=result, created_by=admin.id,
                    created_at=time.time() - 3600, finished_at=time.time() - 3595,
                    approved=True)
            db.add(j); db.flush()
            # 每日简报：默认保留 90 天
            r = Report(name='每日业务复核简报', job_id=j.id,
                       destination='业务复核组 · 本地收件箱', interval_seconds=86400,
                       next_run=time.time() + 86400, enabled=True, retention_days=90)
            db.add(r); db.flush()
            deliver(db, r)
            db.add(Audit(actor='seed', action='demo.baseline',
                         detail={'dataset': d.id}, at=time.time()))

            # 同内容再次投递：复用同一 blob，演示按内容去重、引用计数 > 1
            dup = deliver(db, r)
            # 负责人对第二份投递设置保全
            dup.held = True
            dup.held_at = time.time()
            dup.held_by = 'admin@demo.local'
            dup.hold_reason = '季度复核保全，禁止自动清理'

            # 一份已过保留期的历史投递（保留 1 天，创建于 3 天前），用于演示到期清理
            old = deliver(db, r)
            old.retention_days = 1
            old.created_at = time.time() - 3 * 86400
            old.expires_at = old.created_at + 86400

            # 一份缺物理文件的投递：单独建一个无文件的 blob，模拟外部丢失，
            # 用于演示“缺文件不冒充清理完成、列入失败追查”
            missing_checksum = 'f' * 64
            csv_rel, json_rel = storage.blob_paths(missing_checksum)
            missing_blob = ContentBlob(
                checksum=missing_checksum, csv_path=csv_rel, json_path=json_rel,
                size_bytes=0, ref_count=1, orphan=False, reaped=False,
                created_at=time.time() - 2 * 86400,
                last_referenced_at=time.time() - 2 * 86400)
            db.add(missing_blob); db.flush()
            broken = Delivery(
                report_id=r.id, created_at=time.time() - 2 * 86400,
                checksum=missing_checksum, filename='', destination=r.destination,
                blob_id=missing_blob.id, retention_days=1,
                expires_at=time.time() - 86400, held=False, state='active',
                size_bytes=0)
            db.add(broken); db.flush()
            broken.filename = f'report-{r.id}-delivery-{broken.id}.csv'
        db.commit()
        counts = {model.__tablename__: db.scalar(select(func.count()).select_from(model))
                  for model in [User, Dataset, Job, Report, Delivery, ContentBlob, Audit]}
        print(json.dumps(counts, ensure_ascii=False))
        return counts

if __name__ == '__main__':
    seed()
