# AI-Powered Transaction Processing Pipeline

An asynchronous, containerized financial transaction processing API. The pipeline accepts dirty CSV transaction data, performs data cleaning, runs statistical anomaly detection, uses the Gemini 1.5 Flash LLM to categorize transactions and generate natural-language spending summaries, and stores the structured results in a PostgreSQL database.

---

## 🛠️ Required Stack
- **API Framework**: FastAPI
- **Database**: PostgreSQL (SQLAlchemy ORM)
- **Job Queue**: Celery + Redis Broker
- **LLM Integration**: Google Gemini 1.5 Flash API
- **Containerisation**: Docker and Docker Compose

---

## 🚀 Quick Start (Single Command)

To run the entire system (FastAPI app, Celery worker, Redis, and PostgreSQL), follow these steps:

1. **Clone the repository** and navigate to the project directory:
   ```bash
   cd Backend_DevOps_Assignment
   ```

2. **Configure the Environment**:
   Copy the template env file to `.env`:
   ```bash
   cp .env.example .env
   ```
   Open `.env` and add your **Gemini API Key**:
   ```env
   GEMINI_API_KEY=your_gemini_api_key_here
   ```
   > 💡 **No Key? No Problem!** The pipeline has a built-in **fallback mode**. If no API key is specified, it will automatically fall back to local rule-based mock LLM processing, allowing the application to start and process the file completely out-of-the-box.

3. **Start the System**:
   Run the single-line startup command:
   ```bash
   docker compose up --build
   ```

The API will be available at [http://localhost:8000](http://localhost:8000). Documentation will be accessible at [http://localhost:8000/docs](http://localhost:8000/docs).

---

## 📮 API Endpoints & Example Curl Requests

### 1. Upload CSV (`POST /jobs/upload`)
Accepts a CSV file upload, validates it, creates a Job record with status `pending`, and immediately enqueues the processing task.

**Curl Request:**
```bash
curl -X POST "http://localhost:8000/jobs/upload" \
  -H "accept: application/json" \
  -H "Content-Type: multipart/form-data" \
  -F "file=@transactions.csv"
```

**JSON Response:**
```json
{
  "job_id": "8e147100-4dbe-47d2-b36b-48dfea852097",
  "status": "pending"
}
```

---

### 2. Poll Job Status (`GET /jobs/{job_id}/status`)
Returns the current status of the job (`pending`, `processing`, `completed`, or `failed`). If `completed`, it includes high-level statistics.

**Curl Request:**
```bash
curl "http://localhost:8000/jobs/8e147100-4dbe-47d2-b36b-48dfea852097/status"
```

**JSON Response (Completed):**
```json
{
  "job_id": "8e147100-4dbe-47d2-b36b-48dfea852097",
  "status": "completed",
  "filename": "transactions.csv",
  "row_count_raw": 97,
  "row_count_clean": 90,
  "created_at": "2026-07-01T04:15:43.284518Z",
  "completed_at": "2026-07-01T04:16:30.125632Z",
  "error_message": null,
  "summary": {
    "total_spend_inr": 287413.56,
    "total_spend_usd": 3840.12,
    "anomaly_count": 8,
    "risk_level": "medium"
  }
}
```

---

### 3. Get Job Results (`GET /jobs/{job_id}/results`)
Returns the full structured output: cleaned transactions list, flagged anomalies, and the LLM-generated narrative summary.

**Curl Request:**
```bash
curl "http://localhost:8000/jobs/8e147100-4dbe-47d2-b36b-48dfea852097/results"
```

---

### 4. List All Jobs (`GET /jobs`)
Lists all uploaded jobs with their metadata. Supports filtering via `?status=`.

**Curl Request:**
```bash
curl "http://localhost:8000/jobs?status=completed"
```

---

## 🛠️ The Processing Pipeline

Once Celery dequeues a task, it executes the following steps in sequence:

1. **Data Cleaning**:
   - Dates are parsed from mixed formats (`DD-MM-YYYY`, `YYYY/MM/DD`, `YYYY-MM-DD`) and normalized to ISO 8601 (`YYYY-MM-DD`).
   - Currency symbols (like `$`) are stripped from amounts, and amounts are converted to floats.
   - Transaction status is converted to uppercase.
   - Missing categories are initialized to `'Uncategorised'`.
   - Exact duplicate rows are filtered out.

2. **Anomaly Detection**:
   - **Median Rule**: Outlier flagging if the amount exceeds `3x` the median amount calculated for that `account_id` in the dataset.
   - **Domestic USD Check**: Flagging if currency is `USD` but the merchant is a known domestic brand (`Swiggy`, `Ola`, `IRCTC`).

3. **LLM Classification**:
   - For rows missing a category, the worker batches transactions and sends them to Gemini 1.5 Flash to classify them into: *Food, Shopping, Travel, Transport, Utilities, Cash Withdrawal, Entertainment, or Other*.
   - **Robust Retry & Backoff**: Includes exponential backoff retries. If all fail, the batch is marked as `llm_failed` and falls back to a rule-based regex classifier to ensure the pipeline proceeds.

4. **LLM Narrative Summary**:
   - Aggregated statistics (total spend, top merchants, anomaly counts) are calculated.
   - Gemini 1.5 Flash writes a 2-3 sentence spending narrative and determines the threat risk level (`low`, `medium`, or `high`).

---

## 🔍 Local Verification & Tests

We have written a comprehensive suite of unit and integration tests.

### Run Unit & Integration Tests:
Run the tests locally in the workspace using:
```bash
python3 -m app.test_pipeline
python3 -m app.test_integration
```

---

## 🧠 System Design, Bottlenecks & Scale

### 1. System Architecture & Request Lifecycle
```mermaid
graph TD
    Client[Client / Curl] -->|1. POST /jobs/upload| API[FastAPI Web Server]
    API -->|2. Create pending Job record| DB[(PostgreSQL DB)]
    API -->|3. Push Task to Queue| Broker[Redis Broker]
    API -->|4. Return job_id| Client
    
    Broker -->|5. Dequeue Task| Worker[Celery Background Worker]
    Worker -->|6. Process Pipeline: Clean & Anomaly| Worker
    Worker -->|7. Batch Classify & Summarize| LLM[Gemini 1.5 Flash API]
    Worker -->|8. Save transactions & summary| DB
    Worker -->|9. Update Job status to 'completed'| DB
    
    Client -->|10. GET /jobs/{job_id}/results| API
    API -->|11. Query results| DB
    API -->|12. Return cleaned transactions & summary| Client
```

### 2. Bottlenecks & Scale (100x Traffic Growth)
If tomorrow application traffic scales by 100x, here are the breaking points:
- **Database Connections**: With many simultaneous requests, the PostgreSQL pool will run out of connections. *Solution*: Implement connection pooling via **PgBouncer** and optimize SQLAlchemy connection pool options.
- **Redis Queue Overhead**: Memory consumption on Redis might bottleneck if millions of task payloads are enqueued. *Solution*: Store the raw CSV content in an object store (like AWS S3) and pass only the S3 URL to the Celery task, rather than passing the raw string contents in the message payload.
- **LLM Rate Limits (Gemini API)**: Batching minimizes calls, but 100x traffic will trigger API rate limits (HTTP 429). *Solution*: Introduce token-bucket rate limiting on the worker side, use an enterprise LLM gateway with failover/caching, and queue tasks for slower execution during spikes.
