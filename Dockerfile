FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies + Redis server
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    redis-server \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies from app directory
COPY app/requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy all application code
COPY app/ /app/
COPY start.sh /app/

# Make startup script executable
RUN chmod +x /app/start.sh

EXPOSE 8000

# Execute the startup script
CMD ["/app/start.sh"]
