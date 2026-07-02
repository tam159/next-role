"""Python reconstruction of the Go core-api-grpc data-plane server.

Serves the 7 data-plane gRPC services (Assistants, Threads, Runs, Crons, Admin,
Cache, Checkpointer) + gRPC health, backed by Postgres + Redis, on :50052.

Any RPC not yet implemented natively is transparently forwarded to the original
Go server (CORE_SERVER_GO_FALLBACK, default localhost:50051) so the system runs
end-to-end while services are ported one at a time. Set CORE_SERVER_GO_FALLBACK=""
to disable forwarding and run fully native.
"""
