FROM python:3.10-slim

# Install dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY data/ /app/data/
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY kafka_producer.py .
CMD ["bash", "-c", "python kafka_producer.py"] 