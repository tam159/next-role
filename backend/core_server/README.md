# core_server — Python reconstruction of `core-api-grpc`

A Python (grpc.aio + psycopg + redis) reimplementation of the Go data-plane
server, serving the 7 data-plane gRPC services + gRPC health on **:50052**.

The contract comes from the extracted protos / shipped stubs
(`grpc_common/proto/*_pb2*`). The DB schema is the one the Go server
created (tables `assistant`, `thread`, `run`, `cron`, `checkpoints`,
`checkpoint_blobs`, `checkpoint_writes`, `store`, `thread_ttl`).

## How it runs incrementally

Any RPC **not yet implemented natively** is transparently forwarded to the
original Go server (`CORE_SERVER_GO_FALLBACK`, default `localhost:50051`), so the
whole system works end-to-end from day one and each method can be replaced and
A/B-checked against the Go reference. Set `CORE_SERVER_GO_FALLBACK=""` to run
fully native (no Go dependency).

## Status

| Service       | Status            |
|---------------|-------------------|
| Assistants    | ✅ native (8/8)   |
| Threads       | ✅ native (11/11) |
| Crons         | ✅ native (8/8)   |
| Cache         | ✅ native (2/2, Redis) |
| Admin         | ✅ native (1/1)   |
| Checkpointer  | ✅ native (9/9) — mgmt (Delete/Copy/Prune/Caps) via SQL; data methods (Put/GetTuple/List/PutWrites) are MongoDB-only and raise UNIMPLEMENTED. **Postgres persists checkpoints in-process (direct-PG), bypassing this service entirely.** |
| Runs          | ✅ native (15/15) — CRUD + Create + Next queue (SKIP LOCKED + Redis BLPOP) + Stream/Enter/Publish/MarkDone/Cancel over Redis |
| Health        | ✅ native         |

**100% native — the Go binary is fully retired.** With `CORE_SERVER_GO_FALLBACK=""` every service reports `forwarded=0`. react_agent runs end-to-end (`/runs/wait` + `/runs/stream`, run `success`, checkpoints persisted) against the Python data plane — verified both as a local process and as the containerized `core-server` compose service, with no Go container running.

## Run (local dev — recommended)

Postgres + Redis stay in Docker (`docker compose up -d postgres redis`); the Go
container stays up only as the fallback while services are being ported.

```bash
# 1) start the data-plane server (reads .env for PG/Redis)
PYTHONPATH=. python3 -m core_server         # listens on :50052

# 2) start the API against it (config default already points at :50052)
export LANGSERVE_GRAPHS='{"react_agent": "api/react_agent/graph.py:graph"}'
export DATABASE_URI="postgresql://langgraph_clone:langgraph_clone@localhost:5406/langgraph_clone?sslmode=disable"
uvicorn api.server:app --log-config logging.json --host 0.0.0.0 --port 8005 --no-access-log --reload
```

Env knobs: `CORE_SERVER_BIND` (default `0.0.0.0:50052`), `CORE_SERVER_GO_FALLBACK`
(default `localhost:50051`, set `""` for fully native), `CORE_SERVER_POSTGRES_URI`,
`CORE_SERVER_REDIS_URI`.

## Deploy (containerized — replaces the Go sidecar)

`core_server/Dockerfile` builds the server, and the `core-server` service in
`docker-compose.yml` runs it on :50052 (Go fallback off), connecting to
`postgres:5432` / `redis:6379` over the compose network. The original Go
`core-api-grpc` service is commented out / superseded.

```bash
docker compose up -d --build core-server   # brings up postgres + redis + core-server

# API stays local, pointed at the container (config default :50052):
export LANGSERVE_GRAPHS='{"react_agent": "api/react_agent/graph.py:graph"}'
export DATABASE_URI="postgresql://langgraph_clone:langgraph_clone@localhost:5406/langgraph_clone?sslmode=disable"
uvicorn api.server:app --log-config logging.json --host 0.0.0.0 --port 8005 --no-access-log --reload
```
