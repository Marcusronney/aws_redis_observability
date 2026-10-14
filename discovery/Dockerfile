
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV REDIS_NAMESPACE=redis-observability
ENV REDIS_INVENTORY=/app/discovery/config/inventory-local.yaml

COPY discovery/requirements.txt /app/discovery/requirements.txt

RUN pip install --no-cache-dir \
    -r /app/discovery/requirements.txt

COPY discovery/main.py /app/discovery/main.py

RUN mkdir -p /app/discovery/config

USER 10001:10001

ENTRYPOINT ["python", "/app/discovery/main.py"]

CMD ["--apply"]
