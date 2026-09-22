# syntax=docker/dockerfile:1
# Multi-stage: build the Vite frontend, then run FastAPI + uvicorn with ffmpeg.
FROM node:22-bookworm AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN cd frontend && npm ci
COPY frontend ./frontend
COPY shared ./shared
RUN cd frontend && npm run build

FROM python:3.11-slim-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend ./backend
COPY shared ./shared
COPY demo/script ./demo/script
COPY demo/audio/incident_01.wav ./demo/audio/incident_01.wav
COPY demo/audio/incident_01.groundtruth.json ./demo/audio/incident_01.groundtruth.json
COPY --from=frontend /src/frontend/dist ./frontend/dist

ENV PYTHONUNBUFFERED=1
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}"]
