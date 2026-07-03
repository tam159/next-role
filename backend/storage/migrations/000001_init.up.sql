-- NextRole Agent Server — consolidated database schema (migration 000001).
--
-- Single squashed migration: the complete schema in one file, applied by the
-- backend at boot (server/runtime_postgres/database.py, one schema_migrations
-- row per version). Future schema changes go in new files numbered 000002+ —
-- never edit this one after it has been applied anywhere.
--
-- Notes:
-- * schema_migrations itself is created by the migration runner, not here.
-- * Referential integrity is app-enforced by core-server: run/checkpoint
--   tables intentionally carry no FKs (write-path lock avoidance, async GC).
-- * The run table doubles as the work queue; the partial indexes on
--   status='pending'/'running' are what keep queue claims index-only.

CREATE EXTENSION IF NOT EXISTS btree_gin WITH SCHEMA public;

CREATE EXTENSION IF NOT EXISTS ltree WITH SCHEMA public;

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

CREATE TABLE public.assistant (
    assistant_id uuid DEFAULT gen_random_uuid() NOT NULL,
    graph_id text NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    name text,
    description text,
    context jsonb
);

CREATE TABLE public.assistant_versions (
    assistant_id uuid NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    graph_id text NOT NULL,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    name text,
    description text,
    context jsonb
);

CREATE TABLE public.checkpoint_blobs (
    thread_id uuid NOT NULL,
    channel text NOT NULL,
    version text NOT NULL,
    type text NOT NULL,
    blob bytea,
    checkpoint_ns text DEFAULT ''::text NOT NULL
);

CREATE TABLE public.checkpoint_delete_queue (
    id bigint NOT NULL,
    thread_id uuid NOT NULL,
    checkpoint_ns text DEFAULT ''::text NOT NULL,
    checkpoint_id text NOT NULL,
    enqueued_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.checkpoint_delete_queue_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.checkpoint_delete_queue_id_seq OWNED BY public.checkpoint_delete_queue.id;

CREATE TABLE public.checkpoint_writes (
    thread_id uuid NOT NULL,
    checkpoint_id uuid NOT NULL,
    task_id uuid NOT NULL,
    idx integer NOT NULL,
    channel text NOT NULL,
    type text NOT NULL,
    blob bytea NOT NULL,
    checkpoint_ns text DEFAULT ''::text NOT NULL
);

CREATE TABLE public.checkpoints (
    thread_id uuid NOT NULL,
    checkpoint_id uuid NOT NULL,
    run_id uuid,
    parent_checkpoint_id uuid,
    checkpoint jsonb NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    checkpoint_ns text DEFAULT ''::text NOT NULL
);

CREATE TABLE public.cron (
    cron_id uuid DEFAULT gen_random_uuid() NOT NULL,
    assistant_id uuid,
    thread_id uuid,
    user_id text,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    schedule text NOT NULL,
    next_run_date timestamp with time zone,
    end_time timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    on_run_completed character varying(20) DEFAULT 'delete'::character varying,
    enabled boolean DEFAULT true NOT NULL,
    timezone text,
    CONSTRAINT cron_on_run_completed_check CHECK (((on_run_completed)::text = ANY ((ARRAY['delete'::character varying, 'keep'::character varying])::text[])))
);

CREATE TABLE public.run (
    run_id uuid DEFAULT gen_random_uuid() NOT NULL,
    thread_id uuid NOT NULL,
    assistant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    kwargs jsonb NOT NULL,
    multitask_strategy text DEFAULT 'reject'::text NOT NULL
)
WITH (autovacuum_vacuum_scale_factor='0.01', autovacuum_vacuum_threshold='50', autovacuum_analyze_scale_factor='0.01', autovacuum_analyze_threshold='50');

CREATE TABLE public.store (
    prefix text NOT NULL,
    key text NOT NULL,
    value jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    expires_at timestamp with time zone,
    ttl_minutes integer
);

CREATE TABLE public.thread (
    thread_id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text DEFAULT 'idle'::text NOT NULL,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    "values" jsonb,
    interrupts jsonb DEFAULT '{}'::jsonb NOT NULL,
    error bytea,
    state_updated_at timestamp with time zone,
    configured_ttl_strategy text,
    configured_ttl_minutes numeric
);

CREATE TABLE public.thread_ttl (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    thread_id uuid NOT NULL,
    strategy text DEFAULT 'delete'::text NOT NULL,
    ttl_minutes numeric NOT NULL,
    created_at timestamp without time zone DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'::text) NOT NULL,
    expires_at timestamp without time zone GENERATED ALWAYS AS ((created_at + ((ttl_minutes)::double precision * '00:01:00'::interval))) STORED,
    CONSTRAINT thread_ttl_ttl_minutes_check CHECK ((ttl_minutes >= (0)::numeric))
);

ALTER TABLE ONLY public.checkpoint_delete_queue ALTER COLUMN id SET DEFAULT nextval('public.checkpoint_delete_queue_id_seq'::regclass);

ALTER TABLE ONLY public.assistant
    ADD CONSTRAINT assistant_pkey PRIMARY KEY (assistant_id);

ALTER TABLE ONLY public.assistant_versions
    ADD CONSTRAINT assistant_versions_pkey PRIMARY KEY (assistant_id, version);

ALTER TABLE ONLY public.checkpoint_blobs
    ADD CONSTRAINT checkpoint_blobs_pkey PRIMARY KEY (thread_id, checkpoint_ns, channel, version);

ALTER TABLE ONLY public.checkpoint_delete_queue
    ADD CONSTRAINT checkpoint_delete_queue_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.checkpoint_writes
    ADD CONSTRAINT checkpoint_writes_pkey PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx);

ALTER TABLE ONLY public.checkpoints
    ADD CONSTRAINT checkpoints_pkey PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id);

ALTER TABLE ONLY public.cron
    ADD CONSTRAINT cron_pkey PRIMARY KEY (cron_id);

ALTER TABLE ONLY public.run
    ADD CONSTRAINT run_pkey PRIMARY KEY (run_id);

ALTER TABLE ONLY public.store
    ADD CONSTRAINT store_pkey PRIMARY KEY (prefix, key);

ALTER TABLE ONLY public.thread
    ADD CONSTRAINT thread_pkey PRIMARY KEY (thread_id);

ALTER TABLE ONLY public.thread_ttl
    ADD CONSTRAINT thread_ttl_pkey PRIMARY KEY (id);

CREATE INDEX assistant_created_at_idx ON public.assistant USING btree (created_at DESC);

CREATE INDEX assistant_graph_id_idx ON public.assistant USING btree (graph_id, created_at DESC);

CREATE INDEX assistant_metadata_idx ON public.assistant USING gin (metadata jsonb_path_ops);

CREATE INDEX checkpoints_checkpoint_id_idx ON public.checkpoints USING btree (thread_id, checkpoint_id DESC);

CREATE INDEX checkpoints_run_id_idx ON public.checkpoints USING btree (run_id);

CREATE UNIQUE INDEX idx_cdq_dedup ON public.checkpoint_delete_queue USING btree (thread_id, checkpoint_ns, checkpoint_id);

CREATE INDEX idx_cron_next_run_enabled ON public.cron USING btree (next_run_date) WHERE (enabled = true);

CREATE INDEX idx_run_thread_running ON public.run USING btree (thread_id) WHERE (status = 'running'::text);

CREATE INDEX idx_store_expires_at ON public.store USING btree (expires_at) WHERE (expires_at IS NOT NULL);

CREATE INDEX idx_thread_ttl_expires_at ON public.thread_ttl USING btree (expires_at);

CREATE UNIQUE INDEX idx_thread_ttl_thread_strategy ON public.thread_ttl USING btree (thread_id, strategy);

CREATE INDEX run_pending_by_thread_time_cover ON public.run USING btree (thread_id, created_at, run_id) WHERE (status = 'pending'::text);

CREATE INDEX run_thread_id_idx ON public.run USING btree (thread_id);

CREATE INDEX store_prefix_idx ON public.store USING btree (prefix text_pattern_ops);

CREATE INDEX thread_assistant_id_idx ON public.thread USING btree (((metadata ->> 'assistant_id'::text)), COALESCE(state_updated_at, updated_at) DESC, thread_id DESC) WHERE (metadata ? 'assistant_id'::text);

CREATE INDEX thread_created_at_idx ON public.thread USING btree (created_at DESC);

CREATE INDEX thread_ls_user_id_idx ON public.thread USING btree (((metadata ->> 'ls_user_id'::text)), COALESCE(state_updated_at, updated_at) DESC, thread_id DESC) WHERE (metadata ? 'ls_user_id'::text);

CREATE INDEX thread_metadata_idx ON public.thread USING gin (metadata jsonb_path_ops);

CREATE INDEX thread_owner_updated_idx ON public.thread USING btree (((metadata ->> 'owner'::text)), updated_at DESC, thread_id DESC) WHERE (metadata ? 'owner'::text);

CREATE INDEX thread_status_idx ON public.thread USING btree (status, created_at DESC);

CREATE INDEX thread_values_idx ON public.thread USING gin ("values" jsonb_path_ops);

ALTER TABLE ONLY public.assistant_versions
    ADD CONSTRAINT assistant_versions_assistant_id_fkey FOREIGN KEY (assistant_id) REFERENCES public.assistant(assistant_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.cron
    ADD CONSTRAINT cron_assistant_id_fkey FOREIGN KEY (assistant_id) REFERENCES public.assistant(assistant_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.cron
    ADD CONSTRAINT cron_thread_id_fkey FOREIGN KEY (thread_id) REFERENCES public.thread(thread_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.thread_ttl
    ADD CONSTRAINT thread_ttl_thread_id_fkey FOREIGN KEY (thread_id) REFERENCES public.thread(thread_id) ON DELETE CASCADE;
