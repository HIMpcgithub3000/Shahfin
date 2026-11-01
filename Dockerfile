# syntax=docker/dockerfile:1
FROM python:3.11-slim

# Set environment flags
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# Install minimal OS dependencies for healthchecks and wheel compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first to leverage Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Ensure storage directories exist
RUN mkdir -p audio_recordings data

# Expose HTTP port for FastAPI & Voicebot Console
EXPOSE 8000

# Periodic health check
HEALTHCHECK --interval=20s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/customers || exit 1

# Default startup command: Launch via run.py (seeds database & runs Uvicorn on 0.0.0.0:8000)
CMD ["python", "run.py"]

