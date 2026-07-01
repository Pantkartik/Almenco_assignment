FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/workspace

WORKDIR /workspace

# Install system dependencies + Redis server
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    redis-server \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY app/requirements.txt /workspace/app/
RUN pip install --no-cache-dir -r /workspace/app/requirements.txt

# Copy all application code preserving app package
COPY app/ /workspace/app/
COPY start.sh /workspace/

# Make startup script executable
RUN chmod +x /workspace/start.sh

EXPOSE 8000

# Execute the startup script
CMD ["/workspace/start.sh"]
