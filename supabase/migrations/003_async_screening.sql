alter table public.upload_batches add column if not exists skipped integer not null default 0;

create table if not exists public.batch_items (
    id varchar(36) primary key,
    owner_id varchar(128) not null,
    batch_id varchar(36) not null references public.upload_batches(id) on delete cascade,
    application_id varchar(36) references public.applications(id) on delete set null,
    task_id varchar(36), filename varchar(255) not null, checksum varchar(64),
    status varchar(32) not null, error text,
    created_at timestamptz not null, updated_at timestamptz not null
);

create table if not exists public.agent_runs (
    id varchar(36) primary key, owner_id varchar(128) not null,
    application_id varchar(36) not null references public.applications(id) on delete cascade,
    workflow varchar(80) not null, trigger varchar(80) not null, status varchar(32) not null,
    current_node varchar(80) not null, provider varchar(80) not null, model varchar(200),
    prompt_version varchar(120) not null, fallback_reason text, idempotency_key varchar(255) not null unique,
    error text, started_at timestamptz, finished_at timestamptz, created_at timestamptz not null
);

create table if not exists public.agent_steps (
    id varchar(36) primary key, owner_id varchar(128) not null,
    run_id varchar(36) not null references public.agent_runs(id) on delete cascade,
    node varchar(80) not null, status varchar(32) not null, attempt integer not null,
    latency_ms integer, error text, metadata jsonb not null default '{}'::jsonb, created_at timestamptz not null
);

create table if not exists public.agent_tasks (
    id varchar(36) primary key, owner_id varchar(128) not null,
    application_id varchar(36) not null references public.applications(id) on delete cascade,
    batch_id varchar(36) not null references public.upload_batches(id) on delete cascade,
    batch_item_id varchar(36) not null references public.batch_items(id) on delete cascade,
    run_id varchar(36) not null references public.agent_runs(id) on delete cascade,
    status varchar(32) not null, idempotency_key varchar(255) not null unique, queue_job_id varchar(255),
    attempts integer not null, max_attempts integer not null, last_error text,
    scheduled_at timestamptz not null, started_at timestamptz, finished_at timestamptz,
    created_at timestamptz not null, updated_at timestamptz not null
);

create index if not exists batch_items_owner_idx on public.batch_items(owner_id);
create index if not exists batch_items_batch_idx on public.batch_items(batch_id);
create index if not exists agent_runs_owner_idx on public.agent_runs(owner_id);
create index if not exists agent_steps_owner_idx on public.agent_steps(owner_id);
create index if not exists agent_tasks_owner_idx on public.agent_tasks(owner_id);
create index if not exists agent_tasks_batch_idx on public.agent_tasks(batch_id);

alter table public.batch_items enable row level security;
alter table public.agent_runs enable row level security;
alter table public.agent_steps enable row level security;
alter table public.agent_tasks enable row level security;

create policy owner_access on public.batch_items for all using (owner_id = auth.uid()::text) with check (owner_id = auth.uid()::text);
create policy owner_access on public.agent_runs for all using (owner_id = auth.uid()::text) with check (owner_id = auth.uid()::text);
create policy owner_access on public.agent_steps for all using (owner_id = auth.uid()::text) with check (owner_id = auth.uid()::text);
create policy owner_access on public.agent_tasks for all using (owner_id = auth.uid()::text) with check (owner_id = auth.uid()::text);
