FROM python:3.12-slim

WORKDIR /app

COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ ./server/
COPY dashboard/ ./dashboard/

WORKDIR /app/server

# Data (SQLite DB + uploaded media) lives on a volume
ENV FLEET_DATA_DIR=/data
VOLUME /data

EXPOSE 8550

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8550"]
