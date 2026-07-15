# Run Recovery

Run recovery is disabled by default. Set `LUCODE_RUN_RECOVERY` only when the
corresponding validation level is intended for the current runtime.

| Value | Behavior |
| --- | --- |
| `off` | Default. Does not initialize the recovery journal. |
| `observe` | Writes journal records but does not reconnect events or resume work. |
| `reconnect` | Supports durable event inspection and client reconnect. Interrupted runs remain diagnostic records. |
| `resume_safe` | Allows a later ordinary user message to receive a constrained recovery envelope. It does not auto-start an interrupted run. |

`resume_safe` is deliberately conservative:

- The current user message remains the only routing input.
- A checkpoint must pass checksum and schema validation before it can become recovery context.
- Existing approvals expire during startup and cannot be reused.
- Final checkpoints can repair one missing assistant message; the history `run_id` check keeps that repair idempotent.
- A dispatched non-idempotent action is marked `unknown` after restart. Browser click/fill/submit, terminal mutations, and external MCP mutations are never automatically replayed.

Journal availability is auxiliary. SQLite initialization or write failures make
recovery degraded, but ordinary sessions and requests stay available. The
runtime health endpoint reports whether the journal is enabled and degraded.

Retention is manual. `RunJournal.prune_terminal_event_payloads(...)` defaults
to dry-run, removes only expired terminal-run event payload detail, and keeps
run records, checkpoints, event sequence, sessions, and evidence references.
