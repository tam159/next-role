# NextRole Superset — stock 6.0.0 plus the three drivers the lean image omits:
# psycopg2 (metadata DB on the shared Postgres), clickhouse-connect (the
# warehouse datasets), redis (Celery broker/results + caches). One image, three
# compose services: `superset` (web), `superset-worker` (celery), and the
# `superset-init` one-shot (migrate + admin + dashboard bundle import).
FROM apache/superset:6.0.0

USER root
RUN . /app/.venv/bin/activate && uv pip install --no-cache-dir \
      psycopg2-binary \
      clickhouse-connect \
      redis
USER superset
