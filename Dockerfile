FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    CP_ENV=prod CP_DB_PATH=/data/control_plane.sqlite ENV_FILE=/run/secrets/cp.env
WORKDIR /app
COPY deploy/requirements-control-plane.txt /app/deploy/requirements-control-plane.txt
RUN pip install --no-cache-dir -r deploy/requirements-control-plane.txt \
    && groupadd --gid 10001 hulchul \
    && useradd --uid 10001 --gid hulchul --no-create-home hulchul \
    && mkdir /data && chown hulchul:hulchul /data
COPY control_plane /app/control_plane
COPY src/__init__.py /app/src/__init__.py
COPY src/operator/__init__.py /app/src/operator/__init__.py
COPY src/operator/contracts /app/src/operator/contracts
COPY .env.example /app/.env.example
USER 10001:10001
EXPOSE 8790
VOLUME ["/data"]
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8790/healthz', timeout=2)" || exit 1
CMD ["python", "-m", "control_plane", "--host", "0.0.0.0", "--port", "8790"]
