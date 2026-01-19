FROM node:20-slim AS frontend-builder

WORKDIR /app/frontend

COPY frontend/package.json ./
RUN npm install

COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./
COPY --from=frontend-builder /app/frontend/dist_v2 /app/frontend/dist
COPY --from=frontend-builder /app/frontend/dist_v2 /opt/frontend-dist

EXPOSE 5006

CMD ["sh", "-c", "if [ ! -f /app/frontend/dist/index.html ] && [ -d /opt/frontend-dist ]; then mkdir -p /app/frontend/dist && cp -r /opt/frontend-dist/* /app/frontend/dist/; fi; exec gunicorn -k uvicorn.workers.UvicornWorker -b 0.0.0.0:5006 app:app"]
