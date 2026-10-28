FROM python:3.12-slim

WORKDIR /app
COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt

COPY grocery_agent grocery_agent
COPY api api
COPY web web

# Bake today's prices into the image, so a cold start (Cloud Run scales to zero) serves at once.
# The app refreshes them in the background once they are more than a day old.
RUN python -c "from grocery_agent import db; db.download(); print(db.build())"

ENV PYTHONUNBUFFERED=1 PORT=8000
EXPOSE 8000
# PORT is set by Cloud Run (8080); docker compose uses the default 8000.
CMD exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips '*'
