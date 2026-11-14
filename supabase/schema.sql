-- Primesight state for the serverless deploy (primesight/store.py). Only the server's secret key reads or writes:
-- RLS on with no policies, so the public (publishable) key gets nothing.
create table if not exists kv (key text primary key, value jsonb, updated_at timestamptz default now());
create table if not exists events (id bigserial primary key, stream text not null, data jsonb not null, created_at timestamptz default now());
create index if not exists events_stream_id on events (stream, id);
alter table kv enable row level security;
alter table events enable row level security;
-- Images made during a run (floor maps, approach maps): public read, so phones and projectors can open them.
insert into storage.buckets (id, name, public) values ('images', 'images', true) on conflict (id) do nothing;
