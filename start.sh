#!/bin/bash

# Start Redis in daemon mode (background)
echo "Starting local Redis broker..."
redis-server --daemonize yes

# Start Celery background worker
echo "Starting Celery worker process..."
celery -A tasks.celery_app worker --loglevel=info &

# Start FastAPI web server in the foreground
echo "Starting FastAPI web server..."
# Default Render port is $PORT, fallback to 8000
PORT_VAL=${PORT:-8000}
uvicorn main:app --host 0.0.0.0 --port $PORT_VAL
