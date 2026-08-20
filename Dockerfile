# Waterline agent service — Cloud Run target. Build from the project root:
#   docker build -t waterline-agent -f Dockerfile .
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PORT=8080
RUN pip install --no-cache-dir "google-adk[db]==2.7.1" "google-genai>=2.19" \
    "psycopg[binary]>=3.2" shapely httpx fastapi uvicorn python-dotenv sqlalchemy pg8000
COPY agent/waterline ./waterline
COPY data/captures ./data/captures
CMD ["sh", "-c", "uvicorn waterline.service:app --host 0.0.0.0 --port ${PORT}"]
