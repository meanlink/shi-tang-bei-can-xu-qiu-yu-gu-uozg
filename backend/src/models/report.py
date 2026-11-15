from sqlalchemy import String, Double, ForeignKey, Boolean, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base

class Report(Base):
    __tablename__ = 'reports'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    job_id: Mapped[int] = mapped_column(ForeignKey('jobs.id'))
    destination: Mapped[str] = mapped_column(String(120))
    interval_seconds: Mapped[int]
    next_run: Mapped[float] = mapped_column(Double)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 计划级默认保留天数；空表示长期保留（不自动到期）
    retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

class Delivery(Base):
    __tablename__ = 'deliveries'
    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey('reports.id'))
    created_at: Mapped[float] = mapped_column(Double)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    filename: Mapped[str] = mapped_column(String(160))
    destination: Mapped[str] = mapped_column(String(120))
    # 内容去重：同 checksum 的投递共用一个 content blob；nullable 兼容历史行（迁移补齐）
    blob_id: Mapped[int | None] = mapped_column(ForeignKey('content_blobs.id'), nullable=True, index=True)
    # 保留与保全
    retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    expires_at: Mapped[float | None] = mapped_column(Double, nullable=True, index=True, default=None)
    held: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    held_at: Mapped[float | None] = mapped_column(Double, nullable=True, default=None)
    held_by: Mapped[str | None] = mapped_column(String(150), nullable=True, default=None)
    hold_reason: Mapped[str | None] = mapped_column(String(300), nullable=True, default=None)
    # 生命周期与清理轨迹
    state: Mapped[str] = mapped_column(String(20), default='active', index=True)
    cleaned_at: Mapped[float | None] = mapped_column(Double, nullable=True, default=None)
    cleanup_run_id: Mapped[int | None] = mapped_column(ForeignKey('cleanup_runs.id'), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)

class ContentBlob(Base):
    """按内容（SHA256）去重的物理文件。多份投递引用同一 blob 时只存一份。"""
    __tablename__ = 'content_blobs'
    id: Mapped[int] = mapped_column(primary_key=True)
    checksum: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # 内容寻址相对路径：ab/<sha256前2位>/<sha256>.csv 与 .json
    csv_path: Mapped[str] = mapped_column(String(220))
    json_path: Mapped[str] = mapped_column(String(220))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    ref_count: Mapped[int] = mapped_column(Integer, default=0)
    # 引用归零后进入待回收；新归档同内容会重新激活，绝不删活跃文件
    orphan: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    reaped: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    reaped_at: Mapped[float | None] = mapped_column(Double, nullable=True, default=None)
    created_at: Mapped[float] = mapped_column(Double)
    last_referenced_at: Mapped[float] = mapped_column(Double)

class CleanupRun(Base):
    """一次清理批次。中断后可凭状态续跑，重试不重复释放引用。"""
    __tablename__ = 'cleanup_runs'
    id: Mapped[int] = mapped_column(primary_key=True)
    triggered_by: Mapped[str] = mapped_column(String(150))
    started_at: Mapped[float] = mapped_column(Double, index=True)
    finished_at: Mapped[float | None] = mapped_column(Double, nullable=True, default=None)
    status: Mapped[str] = mapped_column(String(20), default='running', index=True)
    # 扫描候选投递的最大 id 水位，断点续扫
    last_delivery_id: Mapped[int] = mapped_column(Integer, default=0)
    total_candidates: Mapped[int] = mapped_column(Integer, default=0)
    cleaned_count: Mapped[int] = mapped_column(Integer, default=0)
    held_count: Mapped[int] = mapped_column(Integer, default=0)
    missing_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    # 本批次逻辑释放的字节（按 blob 大小在引用归零时计入）
    reclaimed_bytes: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

class CleanupItem(Base):
    """批次内每个投递的处理明细，唯一约束保证同一 run 内幂等。"""
    __tablename__ = 'cleanup_items'
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('cleanup_runs.id'), index=True)
    delivery_id: Mapped[int] = mapped_column(ForeignKey('deliveries.id'), index=True)
    result: Mapped[str] = mapped_column(String(20), default='')
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None)
    reclaimed_bytes: Mapped[int] = mapped_column(Integer, default=0)
    processed_at: Mapped[float | None] = mapped_column(Double, nullable=True, default=None)
