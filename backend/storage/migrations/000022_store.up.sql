create table IF NOT EXISTS store (
    prefix text not null,
    key text not null,
    value jsonb not null,
    primary key (prefix, key)
);
