# Workbench contract boundary

The Workbench owns the public `conversation_id` and `run_id` contract. The
Harness `session_id` is an internal scheduler key kept only for compatibility
routes. Public material references are `material_id` values; storage paths and
provider objects never cross the HTTP boundary.

Run submission is idempotent on `(account_id, conversation_id,
client_request_id)`. A retry uses a new client request id and records
`retry_of_run_id`. NDJSON stream events carry a per-run `sequence`; clients
resume with `after_sequence` and discard already accepted events. `done` only
closes visible text; the lifecycle snapshot remains authoritative.

Conversation history stores the run id on each message and keeps final output
separate from `partial_output`. Stopped, interrupted, and failed runs retain
the visible partial answer. Conversation lifecycle is explicit: `POST
/archive` is reversible soft archive, `POST /restore` removes the archive
marker, and `DELETE` permanently deletes an inactive Conversation and its
durable children. Permanent deletion is refused while a persisted Run is
queued, running, or waiting for input. An archived Conversation remains
readable for its owner and an active Run can still write its terminal state
back to that archived row.

Materials are account-owned. A Conversation can use a material only through
`conversation_materials`; unbinding removes that access without deleting the
account material. Uploading a file establishes `uploaded`. Passage preview
moves it through `parsing` to `ready`, `failed`, or `unsupported`; invalid
transitions are rejected.

Projects are account-owned database objects. Conversation creation accepts an
optional same-account `project_id`; move validates the same account; deleting
a Project clears `Conversation.project_id` and preserves the Conversation.
