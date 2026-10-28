FROM python:3.12-slim

WORKDIR /app
COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt

COPY grocery_agent grocery_agent
COPY api api
COPY web web

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
# Prices are downloaded into /app/data on first start and refreshed daily (keep it on a volume).
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
