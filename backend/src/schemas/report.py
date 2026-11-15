from pydantic import BaseModel, Field
from src.services.reports import RETENTION_MIN_DAYS, RETENTION_MAX_DAYS

class ReportInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    job_id: int
    destination: str = Field(default='业务复核组', min_length=2, max_length=120)
    interval_seconds: int = Field(default=86400, ge=60, le=2592000)
    delay_seconds: int = Field(default=0, ge=0, le=2592000)
    retention_days: int | None = Field(
        default=None, ge=RETENTION_MIN_DAYS, le=RETENTION_MAX_DAYS)

class HoldInput(BaseModel):
    hold: bool
    reason: str | None = Field(default=None, max_length=300)

class DeliveryRetentionInput(BaseModel):
    retention_days: int | None = Field(
        default=None, ge=RETENTION_MIN_DAYS, le=RETENTION_MAX_DAYS)

class ReportRetentionInput(BaseModel):
    retention_days: int | None = Field(
        default=None, ge=RETENTION_MIN_DAYS, le=RETENTION_MAX_DAYS)
