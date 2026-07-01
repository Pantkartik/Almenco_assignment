#!/bin/bash

# Start Redis in daemon mode (background)
echo "Starting local Redis broker..."
redis-server --daemonize yes

# Start Celery background worker as a package
echo "Starting Celery worker process..."
celery -A app.tasks.celery_app worker --loglevel=info &

# Start FastAPI web server in the foreground as a package
echo "Starting FastAPI web server..."
PORT_VAL=${PORT:-8000}
uvicorn app.main:app --host 0.0.0.0 --port $PORT_VAL
