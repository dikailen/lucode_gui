create table if not exists schema_meta (
  key text primary key,
  value text not null,
  updated_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

insert into schema_meta(key, value, updated_at)
values ('schema_version', 'context_store.v1', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
on conflict(key) do update set
  value = excluded.value,
  updated_at = excluded.updated_at;

create table if not exists sessions (
  session_id text primary key,
  title text not null default '',
  created_at text not null,
  updated_at text not null,
  source text not null default 'sqlite',
  source_fingerprint text not null default '',
  schema_version text not null default 'context_store.v1',
  metadata_json text not null default '{}'
);

create table if not exists messages (
  message_id text primary key,
  session_id text not null,
  role text not null,
  content text not null,
  created_at text not null,
  source text not null default 'sqlite',
  schema_version text not null default 'context_store.v1',
  metadata_json text not null default '{}',
  foreign key(session_id) references sessions(session_id) on delete cascade
);

create index if not exists idx_messages_session_created
on messages(session_id, created_at);

create table if not exists context_summaries (
  summary_id text primary key,
  session_id text not null,
  summary text not null,
  created_at text not null,
  source text not null default 'sqlite',
  schema_version text not null default 'context_store.v1',
  metadata_json text not null default '{}',
  foreign key(session_id) references sessions(session_id) on delete cascade
);

create index if not exists idx_context_summaries_session_created
on context_summaries(session_id, created_at);

create table if not exists context_ledger_results (
  ledger_id text primary key,
  session_id text not null,
  run_id text not null default '',
  mode text not null default '',
  applied integer not null default 0,
  triggered integer not null default 0,
  estimated_input_tokens integer not null default 0,
  context_window_tokens integer not null default 0,
  created_at text not null,
  schema_version text not null default 'context_store.v1',
  metadata_json text not null default '{}',
  foreign key(session_id) references sessions(session_id) on delete cascade
);

create index if not exists idx_context_ledger_session_created
on context_ledger_results(session_id, created_at);

create table if not exists tool_dehydrated_results (
  result_id text primary key,
  session_id text not null,
  run_id text not null default '',
  tool text not null default '',
  summary text not null default '',
  evidence_ref text not null default '',
  raw_artifact_ref text not null default '',
  created_at text not null,
  schema_version text not null default 'context_store.v1',
  metadata_json text not null default '{}',
  foreign key(session_id) references sessions(session_id) on delete cascade
);

create index if not exists idx_tool_dehydrated_session_created
on tool_dehydrated_results(session_id, created_at);

create table if not exists evidence_refs (
  ref_id text primary key,
  session_id text not null,
  run_id text not null default '',
  evidence_ref text not null,
  artifact_ref text not null default '',
  source_type text not null default '',
  created_at text not null,
  schema_version text not null default 'context_store.v1',
  metadata_json text not null default '{}',
  foreign key(session_id) references sessions(session_id) on delete cascade
);

create index if not exists idx_evidence_refs_session_created
on evidence_refs(session_id, created_at);

create table if not exists skill_metadata_proposals (
  proposal_id text primary key,
  skill_id text not null,
  content_hash text not null,
  source text not null,
  status text not null default 'pending',
  confidence real not null default 0,
  payload_json text not null default '{}',
  reason text not null default '',
  created_at text not null,
  updated_at text not null
);

create unique index if not exists idx_skill_metadata_proposals_version_source
on skill_metadata_proposals(skill_id, content_hash, source);

create index if not exists idx_skill_metadata_proposals_status
on skill_metadata_proposals(skill_id, status, updated_at desc);
