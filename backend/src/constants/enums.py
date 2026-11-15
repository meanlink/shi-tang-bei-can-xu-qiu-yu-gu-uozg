from enum import Enum
class Role(str, Enum):
    admin = 'admin'
    analyst = 'analyst'
    viewer = 'viewer'
class JobStatus(str, Enum):
    queued = 'queued'
    running = 'running'
    completed = 'completed'
    failed = 'failed'
# 投递生命周期：active 在档可下载；cleaned 引用已释放（记录保留）；failed 清理时发现文件缺失等异常
class DeliveryState(str, Enum):
    active = 'active'
    cleaned = 'cleaned'
    failed = 'failed'
# 清理批次状态：running 可中断续跑；completed 全部处理；failed 批次级异常
class CleanupStatus(str, Enum):
    running = 'running'
    completed = 'completed'
    failed = 'failed'
# 清理逐项结果：cleaned 已释放引用；held 命中保全跳过；missing 缺文件失败；skipped 未到期等
class CleanupItemResult(str, Enum):
    cleaned = 'cleaned'
    held = 'held'
    missing = 'missing'
    skipped = 'skipped'
    failed = 'failed'
