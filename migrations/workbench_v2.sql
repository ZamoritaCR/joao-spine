-- Workbench v2 schema (idempotent)
-- Tables: workbench_sessions, workbench_history, workbench_audit_log,
--         workbench_file_uploads, workbench_brain_costs

create extension if not exists pgcrypto;

create table if not exists public.workbench_sessions (
  id uuid primary key default gen_random_uuid(),
  session_name text not null,
  user_handle text not null default johan,
  chosen_option text not null default E,
  status text not null default active,
  meta jsonb not null default {}::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists workbench_sessions_session_name_key
  on public.workbench_sessions (session_name);

create index if not exists workbench_sessions_created_at_idx
  on public.workbench_sessions (created_at desc);

create table if not exists public.workbench_history (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references public.workbench_sessions(id) on delete cascade,
  panel text not null,
  event_type text not null,
  brain text,
  prompt text,
  response text,
  metadata jsonb not null default {}::jsonb,
  latency_ms integer,
  cost_usd numeric(12,6) not null default 0,
  created_at timestamptz not null default now()
);

create index if not exists workbench_history_session_id_idx
  on public.workbench_history (session_id);
create index if not exists workbench_history_created_at_idx
  on public.workbench_history (created_at desc);
create index if not exists workbench_history_panel_idx
  on public.workbench_history (panel);

create table if not exists public.workbench_audit_log (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references public.workbench_sessions(id) on delete cascade,
  phase text not null check (phase in (PROPOSE,CONFIRM,EXECUTE,ROLLBACK)),
  status text not null,
  description text,
  command text,
  confirmation text,
  allowlist_match boolean,
  diff_text text,
  metadata jsonb not null default {}::jsonb,
  created_at timestamptz not null default now(),
  executed_at timestamptz
);

create index if not exists workbench_audit_log_session_id_idx
  on public.workbench_audit_log (session_id);
create index if not exists workbench_audit_log_created_at_idx
  on public.workbench_audit_log (created_at desc);
create index if not exists workbench_audit_log_phase_idx
  on public.workbench_audit_log (phase);

create table if not exists public.workbench_file_uploads (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references public.workbench_sessions(id) on delete cascade,
  file_name text not null,
  mime_type text,
  size_bytes bigint,
  sha256 text,
  storage_path text,
  extracted_text text,
  extracted_metadata jsonb not null default {}::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists workbench_file_uploads_session_id_idx
  on public.workbench_file_uploads (session_id);
create index if not exists workbench_file_uploads_created_at_idx
  on public.workbench_file_uploads (created_at desc);

create table if not exists public.workbench_brain_costs (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references public.workbench_sessions(id) on delete cascade,
  history_id uuid references public.workbench_history(id) on delete set null,
  brain text not null,
  model text,
  prompt_tokens integer,
  completion_tokens integer,
  total_tokens integer,
  cost_usd numeric(12,6) not null default 0,
  latency_ms integer,
  success boolean not null default true,
  created_at timestamptz not null default now()
);

create index if not exists workbench_brain_costs_session_id_idx
  on public.workbench_brain_costs (session_id);
create index if not exists workbench_brain_costs_created_at_idx
  on public.workbench_brain_costs (created_at desc);
create index if not exists workbench_brain_costs_brain_idx
  on public.workbench_brain_costs (brain);

alter table public.workbench_sessions enable row level security;
alter table public.workbench_history enable row level security;
alter table public.workbench_audit_log enable row level security;
alter table public.workbench_file_uploads enable row level security;
alter table public.workbench_brain_costs enable row level security;

drop policy if exists workbench_sessions_service_all on public.workbench_sessions;
create policy workbench_sessions_service_all on public.workbench_sessions
  for all using (auth.role() = service_role) with check (auth.role() = service_role);

drop policy if exists workbench_history_service_all on public.workbench_history;
create policy workbench_history_service_all on public.workbench_history
  for all using (auth.role() = service_role) with check (auth.role() = service_role);

drop policy if exists workbench_audit_log_service_all on public.workbench_audit_log;
create policy workbench_audit_log_service_all on public.workbench_audit_log
  for all using (auth.role() = service_role) with check (auth.role() = service_role);

drop policy if exists workbench_file_uploads_service_all on public.workbench_file_uploads;
create policy workbench_file_uploads_service_all on public.workbench_file_uploads
  for all using (auth.role() = service_role) with check (auth.role() = service_role);

drop policy if exists workbench_brain_costs_service_all on public.workbench_brain_costs;
create policy workbench_brain_costs_service_all on public.workbench_brain_costs
  for all using (auth.role() = service_role) with check (auth.role() = service_role);
