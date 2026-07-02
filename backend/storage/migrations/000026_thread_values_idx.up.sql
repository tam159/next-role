create index concurrently if not exists thread_values_idx on thread using gin (values jsonb_path_ops);
