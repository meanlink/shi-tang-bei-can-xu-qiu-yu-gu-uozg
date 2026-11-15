"""清理/回收互斥锁。

优先使用 Redis 保证多进程（backend、worker）互斥；Redis 不可用时退化到
进程内线程锁，并依靠数据库行状态做最终兜底（同一批次只会被一个执行者领取）。
"""
import contextlib, threading, time, logging
from src.config.settings import REDIS_URL

_process_locks = {}
_process_guard = threading.Lock()

@contextlib.contextmanager
def named_lock(key: str, ttl_seconds: int = 600):
    try:
        from redis import Redis
        client = Redis.from_url(REDIS_URL, socket_timeout=2)
        token = f'{key}-{time.time_ns()}'
        acquired = client.set(key, token, nx=True, ex=ttl_seconds)
        client.close()
        if not acquired:
            raise RuntimeError('另一个清理任务正在执行，请稍后再试')
        try:
            yield
        finally:
            try:
                from redis import Redis as _R
                c = _R.from_url(REDIS_URL, socket_timeout=2)
                # 仅释放自己持有的令牌，避免误删他人锁
                if c.get(key) == token.encode():
                    c.delete(key)
                c.close()
            except Exception:
                logging.exception('释放锁 %s 失败，等待 TTL 自动过期', key)
    except RuntimeError:
        raise
    except Exception:
        # Redis 不可用：进程内锁兜底
        with _process_guard:
            lock = _process_locks.setdefault(key, threading.Lock())
        acquired = lock.acquire(blocking=False)
        if not acquired:
            raise RuntimeError('另一个清理任务正在执行，请稍后再试')
        try:
            yield
        finally:
            lock.release()
