import logging
import os
import uuid
from celery import Celery
from datetime import datetime, timezone
from .config import settings
from .database import SessionLocal
from .models import Job, Transaction, JobSummary
from .pipeline import (
    clean_and_parse_csv_data,
    process_anomaly_detection,
    run_llm_classification,
    generate_summary_and_narrative
)

logger = logging.getLogger("tasks")
logging.basicConfig(level=logging.INFO)

# Initialize Celery app
celery_app = Celery("tasks", broker=settings.REDIS_URL, backend=settings.REDIS_URL)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)

@celery_app.task(name="tasks.process_transaction_csv")
def process_transaction_csv(job_id_str: str, file_content: str):
    logger.info(f"Starting background job: {job_id_str}")
    job_uuid = uuid.UUID(job_id_str)
    
    db = SessionLocal()
    try:
        # Find the job
        job = db.query(Job).filter(Job.id == job_uuid).first()
        if not job:
            logger.error(f"Job not found in database: {job_id_str}")
            return
        
        # 1. Update status to processing
        job.status = "processing"
        db.commit()
        
        # 2. Clean and parse CSV
        logger.info("Step A: Cleaning and parsing CSV...")
        cleaned_rows, raw_count = clean_and_parse_csv_data(file_content)
        job.row_count_raw = raw_count
        db.commit()
        
        # 3. Detect anomalies
        logger.info("Step B: Detecting anomalies...")
        cleaned_rows = process_anomaly_detection(cleaned_rows)
        
        # 4. LLM Classification (batch calls)
        logger.info("Step C: Running LLM classification...")
        groq_api_key = os.environ.get("GROQ_API_KEY", settings.GROQ_API_KEY)
        cleaned_rows = run_llm_classification(cleaned_rows, groq_api_key)
        
        # 5. LLM Summary and Narrative
        logger.info("Step D: Generating narrative summary...")
        summary_data = generate_summary_and_narrative(cleaned_rows, groq_api_key)
        
        # 6. Save data to DB
        logger.info("Saving transactions and summary to database...")
        
        # Write transaction records
        for row in cleaned_rows:
            txn = Transaction(
                job_id=job.id,
                txn_id=row["txn_id"],
                date=row["date"],
                merchant=row["merchant"],
                amount=row["amount"],
                currency=row["currency"],
                status=row["status"],
                category=row["category"],
                account_id=row["account_id"],
                notes=row["notes"],
                is_anomaly=row["is_anomaly"],
                anomaly_reason=row["anomaly_reason"],
                llm_category=row["llm_category"],
                llm_raw_response=row["llm_raw_response"],
                llm_failed=row["llm_failed"]
            )
            db.add(txn)
            
        # Write JobSummary record
        summary = JobSummary(
            job_id=job.id,
            total_spend_inr=summary_data["total_spend_inr"],
            total_spend_usd=summary_data["total_spend_usd"],
            top_merchants=summary_data["top_merchants"],
            anomaly_count=summary_data["anomaly_count"],
            narrative=summary_data["narrative"],
            risk_level=summary_data["risk_level"]
        )
        db.add(summary)
        
        # Update job metadata
        job.row_count_clean = len(cleaned_rows)
        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        
        db.commit()
        logger.info(f"Job completed successfully: {job_id_str}")
        
    except Exception as e:
        logger.exception(f"Error processing job {job_id_str}")
        db.rollback()
        try:
            # Mark job as failed in database
            job = db.query(Job).filter(Job.id == job_uuid).first()
            if job:
                job.status = "failed"
                job.error_message = str(e)
                job.completed_at = datetime.now(timezone.utc)
                db.commit()
        except Exception as db_err:
            logger.error(f"Failed to update failed job status: {db_err}")
            
    finally:
        db.close()
