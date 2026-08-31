create table if not exists public.jobs (id varchar(36) primary key, owner_id varchar(128) not null, title varchar(200) not null, department varchar(120) not null default 'Engineering', location varchar(200) not null default 'Remote', description text not null, requirements jsonb not null default '{}'::jsonb, status varchar(32) not null default 'OPEN', created_at timestamptz not null default now());
create table if not exists public.upload_batches (id varchar(36) primary key, owner_id varchar(128) not null, job_id varchar(36) not null references public.jobs(id) on delete cascade, status varchar(32) not null default 'PROCESSING', total integer not null default 0, completed integer not null default 0, failed integer not null default 0, created_at timestamptz not null default now());
create table if not exists public.applications (id varchar(36) primary key, owner_id varchar(128) not null, job_id varchar(36) not null references public.jobs(id) on delete cascade, batch_id varchar(36) references public.upload_batches(id) on delete set null, candidate_name varchar(200) not null, candidate_email varchar(320) not null default '', status varchar(32) not null default 'WAITING_REVIEW', resume_filename varchar(255), resume_size integer, resume_checksum varchar(64), resume_text text not null, screening jsonb not null default '{}'::jsonb, pipeline jsonb not null default '[]'::jsonb, review jsonb, created_at timestamptz not null default now());
create table if not exists public.interviews (id varchar(36) primary key, owner_id varchar(128) not null, application_id varchar(36) not null references public.applications(id) on delete cascade, start_at timestamptz not null, end_at timestamptz not null, status varchar(32) not null default 'SCHEDULED', meeting_url varchar(500) not null);
create table if not exists public.audit_logs (id varchar(36) primary key, owner_id varchar(128) not null, application_id varchar(36), action varchar(80) not null, metadata jsonb not null default '{}'::jsonb, created_at timestamptz not null default now());

create index if not exists jobs_owner_idx on public.jobs(owner_id);
create index if not exists applications_owner_idx on public.applications(owner_id);
create index if not exists applications_job_idx on public.applications(job_id);
create index if not exists applications_batch_idx on public.applications(batch_id);
create index if not exists applications_checksum_idx on public.applications(resume_checksum);
create index if not exists batches_owner_idx on public.upload_batches(owner_id);
create index if not exists interviews_owner_idx on public.interviews(owner_id);
create index if not exists audit_logs_owner_idx on public.audit_logs(owner_id);

alter table public.jobs enable row level security;
alter table public.upload_batches enable row level security;
alter table public.applications enable row level security;
alter table public.interviews enable row level security;
alter table public.audit_logs enable row level security;

do $$
declare table_name text;
begin
  foreach table_name in array array['jobs','upload_batches','applications','interviews','audit_logs'] loop
    if not exists (select 1 from pg_policies where schemaname = 'public' and tablename = table_name and policyname = 'owner_access') then
      execute format('create policy owner_access on public.%I for all using (owner_id = auth.uid()::text) with check (owner_id = auth.uid()::text)', table_name);
    end if;
  end loop;
end $$;
