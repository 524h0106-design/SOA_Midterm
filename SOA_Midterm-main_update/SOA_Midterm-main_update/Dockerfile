FROM python:3.12-slim
WORKDIR /app
COPY services services
COPY web web
ENV PYTHONUNBUFFERED=1 BIND_HOST=0.0.0.0 DATA_DIR=/app/data
RUN useradd --uid 10001 --create-home appuser && mkdir -p /app/data && chown appuser:appuser /app/data
USER appuser
CMD ["python", "services/gateway.py"]
