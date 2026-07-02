alter table checkpoint_blobs
    alter column version set data type text using lpad(version::text, 32, '0')::text;
