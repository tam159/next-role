# core_server — Python reconstruction of `core-api-grpc`

The Python gRPC data plane for NextRole's agent server, built with `grpc.aio`, psycopg, and Redis.
It owns assistant, thread, run, cron, store, and queue operations and serves seven data-plane
services plus gRPC health on port `50052`.

The contract comes from the generated stubs in [`server/grpc_common/proto/`](../grpc_common/proto/).
The implementation is 100% native; the original Go binary is retired. A compatibility forwarding
hook remains for development, but compose sets `CORE_SERVER_GO_FALLBACK=""`, so every production
path runs in Python.

## Layout

```
server/core_server/
├── __main__.py            # gRPC server entrypoint and service registration
├── settings.py            # Postgres, Redis, bind, pool, and fallback settings
├── db.py                  # psycopg pool and SQL helpers
├── redis_db.py            # run queue doorbell and stream pub/sub
├── _filters.py            # authorization filters translated to parameterized SQL
├── _convert.py            # protobuf ↔ Python conversion
├── _forward.py            # optional compatibility forwarding
└── servicers/
    ├── assistants.py
    ├── threads.py
    ├── runs.py
    ├── crons.py
    ├── cache.py
    ├── admin.py
    └── checkpointer.py
```

## Status

All services are native: Assistants (8/8), Threads (11/11), Crons (8/8), Cache (2/2, Redis),
Admin (1/1), Checkpointer (9/9), Runs (15/15), and gRPC health. Runs combine SQL queue claiming
(`SKIP LOCKED`) with Redis doorbells and pub/sub.

Checkpointer management methods use SQL; MongoDB-only data methods raise `UNIMPLEMENTED`.
Postgres checkpoint persistence runs in-process and bypasses this service.

Both registered graphs (`career_agent` and `analytics_agent`) run through this data plane. Graph
loading remains in the HTTP backend; core-server receives their ids through `LANGSERVE_GRAPHS` for
assistant scoping.

## Run

The supported development path is the root compose stack:

```bash
docker compose up -d
```

The `core-server` service uses the same `backend/Dockerfile` image as the HTTP backend, with a
different command:

```bash
python -m server.core_server
```

It connects to `postgres:5432` and `redis:6379` over the compose network and is internal-only.
Source changes under `server/core_server/` or `server/grpc_common/` require:

```bash
docker compose restart core-server
```

To run it directly, start Postgres and Redis, then invoke the module from `backend/` with
`CORE_SERVER_POSTGRES_URI` and `CORE_SERVER_REDIS_URI` pointing at their host ports.

## Configuration

- `CORE_SERVER_BIND` — listen address; defaults to `0.0.0.0:50052`.
- `CORE_SERVER_POSTGRES_URI` — psycopg connection URI.
- `CORE_SERVER_REDIS_URI` — Redis connection URI.
- `CORE_SERVER_GO_FALLBACK` — optional compatibility endpoint; set to an empty string for the
  fully native mode used by compose.
- `CORE_SERVER_POOL_MIN` / `CORE_SERVER_POOL_MAX` — database pool bounds.
- `CORE_SERVER_MAX_MSG_BYTES` — maximum gRPC message size.
