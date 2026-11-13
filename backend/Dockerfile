FROM pytorch/pytorch:2.2.0-cuda12.1-cudnn8-runtime

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY models/ ./models/
COPY ppe_tracker.py .
COPY .service-key .

ENV PPE_BIND=0.0.0.0 PPE_PORT=8186 PPE_TRACKER_DB=/data/ppe_tracker.db
EXPOSE 8186
VOLUME ["/data"]

CMD ["python", "ppe_tracker.py"]

