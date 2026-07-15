create table if not exists run_recovery_meta (
  key text primary key,
  value text not null,
  updated_at text not null
);

create table if not exists agent_runs (
  run_id text primary key,
  session_id text not null,
  client_request_id text not null default '',
  status text not null,
  recovery_state text not null default 'none',
  interrupted_at text not null default '',
  next_event_seq integer not null default 0,
  lease_owner text not null default '',
  lease_expires_at text not null default '',
  created_at text not null,
  updated_at text not null
);

create index if not exists idx_agent_runs_session_updated
on agent_runs(session_id, updated_at desc, run_id desc);

create table if not exists run_events (
  event_id text primary key,
  run_id text not null,
  session_id text not null,
  seq integer not null,
  event_type text not null,
  payload_json text not null default '{}',
  payload_checksum text not null,
  created_at text not null,
  unique(run_id, seq),
  foreign key(run_id) references agent_runs(run_id) on delete cascade
);

create index if not exists idx_run_events_run_seq
on run_events(run_id, seq);

create table if not exists run_checkpoints (
  checkpoint_id text primary key,
  run_id text not null,
  kind text not null,
  event_seq integer not null default 0,
  state_json text not null,
  compatibility_json text not null,
  checksum text not null,
  created_at text not null,
  foreign key(run_id) references agent_runs(run_id) on delete cascade
);

create index if not exists idx_run_checkpoints_run_created
on run_checkpoints(run_id, created_at desc, checkpoint_id desc);

create table if not exists run_tasks (
  run_id text not null,
  task_id text not null,
  attempt integer not null,
  status text not null,
  task_json text not null default '{}',
  dependency_json text not null default '[]',
  evidence_refs_json text not null default '[]',
  created_at text not null,
  updated_at text not null,
  primary key(run_id, task_id, attempt),
  foreign key(run_id) references agent_runs(run_id) on delete cascade
);

create table if not exists tool_invocations (
  invocation_id text primary key,
  run_id text not null,
  task_id text not null default '',
  attempt integer not null default 0,
  tool_name text not null,
  arguments_hash text not null,
  side_effect_class text not null default 'unknown',
  status text not null,
  evidence_ref text not null default '',
  created_at text not null,
  updated_at text not null,
  foreign key(run_id) references agent_runs(run_id) on delete cascade
);

create table if not exists tool_postconditions (
  invocation_id text primary key,
  kind text not null,
  expectation_json text not null,
  expectation_checksum text not null,
  status text not null default 'pending',
  evidence_ref text not null default '',
  observed_json text not null default '{}',
  event_seq integer not null default 0,
  created_at text not null,
  updated_at text not null,
  foreign key(invocation_id) references tool_invocations(invocation_id) on delete cascade
);

create index if not exists idx_tool_postconditions_status
on tool_postconditions(status, updated_at asc, invocation_id asc);

create table if not exists run_approvals (
  approval_id text primary key,
  run_id text not null,
  invocation_id text not null,
  action_digest text not null,
  status text not null,
  decision text not null default '',
  requested_at text not null,
  resolved_at text not null default '',
  foreign key(run_id) references agent_runs(run_id) on delete cascade
);
