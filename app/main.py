from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from uuid import UUID
from typing import List, Optional
from contextlib import asynccontextmanager

from .database import Base, engine, get_db
from .models import Job, Transaction, JobSummary
from .schemas import (
    UploadResponse,
    JobStatusResponse,
    JobResultsResponse,
    JobResponse,
    JobSummaryStats,
    JobSummaryResponse,
    TransactionResponse
)
from .tasks import process_transaction_csv

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize the database tables on startup
    Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(
    title="AI-Powered Transaction Processing Pipeline API",
    description="FastAPI application to process dirty financial transactions CSV files asynchronously.",
    version="1.0.0",
    lifespan=lifespan
)

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/app/index.html")

# Mount the frontend static files
import os
if os.path.isdir("frontend"):
    app.mount("/app", StaticFiles(directory="frontend"), name="frontend")

@app.post("/jobs/upload", response_model=UploadResponse, status_code=201)
async def upload_transactions_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    # 1. Validate file format
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="Only CSV files are supported.")
        
    try:
        content_bytes = await file.read()
        file_content = content_bytes.decode("utf-8")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read file: {str(e)}")
        
    # 2. Check for empty CSV
    lines = file_content.splitlines()
    if len(lines) < 2:
        raise HTTPException(status_code=400, detail="CSV file must contain a header and at least one transaction row.")
        
    # 3. Create database Job record
    job = Job(
        filename=file.filename,
        status="pending"
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    
    # 4. Enqueue background task
    process_transaction_csv.delay(str(job.id), file_content)
    
    return UploadResponse(job_id=job.id, status=job.status)


@app.get("/jobs/{job_id}/status", response_model=JobStatusResponse)
def get_job_status(job_id: UUID, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    # If job is completed, attach high-level stats summary
    summary_stats = None
    if job.status == "completed" and job.summary:
        summary_stats = JobSummaryStats(
            total_spend_inr=job.summary.total_spend_inr,
            total_spend_usd=job.summary.total_spend_usd,
            anomaly_count=job.summary.anomaly_count,
            risk_level=job.summary.risk_level
        )
        
    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        filename=job.filename,
        row_count_raw=job.row_count_raw,
        row_count_clean=job.row_count_clean,
        created_at=job.created_at,
        completed_at=job.completed_at,
        error_message=job.error_message,
        summary=summary_stats
    )


@app.get("/jobs/{job_id}/results", response_model=JobResultsResponse)
def get_job_results(job_id: UUID, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    if job.status == "pending" or job.status == "processing":
        return JobResultsResponse(
            job_id=job.id,
            status=job.status,
            summary=None,
            cleaned_transactions=[],
            flagged_anomalies=[]
        )
        
    if job.status == "failed":
        raise HTTPException(
            status_code=400,
            detail=f"Job failed with error: {job.error_message}"
        )
        
    # Extract transactions and group anomalies
    cleaned_txns = []
    anomalies = []

    for t in job.transactions:
        txn_resp = TransactionResponse(
            id=t.id,
            txn_id=t.txn_id,
            date=t.date.isoformat() if t.date else None,
            merchant=t.merchant,
            amount=t.amount,
            currency=t.currency,
            status=t.status,
            category=t.category,
            account_id=t.account_id,
            notes=t.notes,
            is_anomaly=t.is_anomaly,
            anomaly_reason=t.anomaly_reason,
            llm_category=t.llm_category,
            llm_failed=t.llm_failed
        )
        cleaned_txns.append(txn_resp)
        if t.is_anomaly:
            anomalies.append(txn_resp)
            
    summary_resp = None
    if job.summary:
        summary_resp = JobSummaryResponse.model_validate(job.summary)
        
    return JobResultsResponse(
        job_id=job.id,
        status=job.status,
        summary=summary_resp,
        cleaned_transactions=cleaned_txns,
        flagged_anomalies=anomalies
    )


@app.get("/jobs", response_model=List[JobResponse])
def list_jobs(
    status: Optional[str] = Query(None, description="Filter jobs by status"),
    db: Session = Depends(get_db)
):
    query = db.query(Job)
    if status:
        query = query.filter(Job.status == status.lower().strip())
        
    jobs = query.order_by(Job.created_at.desc()).all()
    return jobs
