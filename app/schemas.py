from pydantic import BaseModel, ConfigDict
from uuid import UUID
import datetime as dt
from typing import Optional, List, Any


class JobResponse(BaseModel):
    id: UUID
    filename: str
    status: str
    row_count_raw: int
    row_count_clean: Optional[int] = None
    created_at: dt.datetime
    completed_at: Optional[dt.datetime] = None

    model_config = ConfigDict(from_attributes=True)


class JobSummaryStats(BaseModel):
    total_spend_inr: float
    total_spend_usd: float
    anomaly_count: int
    risk_level: Optional[str] = None


class JobStatusResponse(BaseModel):
    job_id: UUID
    status: str
    filename: str
    row_count_raw: int
    row_count_clean: Optional[int] = None
    created_at: dt.datetime
    completed_at: Optional[dt.datetime] = None
    error_message: Optional[str] = None
    summary: Optional[JobSummaryStats] = None

    model_config = ConfigDict(from_attributes=True)


class TransactionResponse(BaseModel):
    id: int
    txn_id: Optional[str] = None
    date: Optional[str] = None  # Returned as ISO string (YYYY-MM-DD) to avoid name collision
    merchant: str
    amount: float
    currency: str
    status: str
    category: str
    account_id: str
    notes: Optional[str] = None
    is_anomaly: bool
    anomaly_reason: Optional[str] = None
    llm_category: Optional[str] = None
    llm_failed: bool


class JobSummaryResponse(BaseModel):
    total_spend_inr: float
    total_spend_usd: float
    top_merchants: List[Any]
    anomaly_count: int
    narrative: Optional[str] = None
    risk_level: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class JobResultsResponse(BaseModel):
    job_id: UUID
    status: str
    summary: Optional[JobSummaryResponse] = None
    cleaned_transactions: List[TransactionResponse] = []
    flagged_anomalies: List[TransactionResponse] = []


class UploadResponse(BaseModel):
    job_id: UUID
    status: str
