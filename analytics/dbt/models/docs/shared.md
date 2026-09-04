{% docs owner %}
The product user that owns the record: the Better Auth user id (`dim_user.user_id`).
`'default'` marks rows created in single-user mode (auth disabled, no login) — this is also how all
pre-auth history appears, so per-user questions should exclude `'default'` or treat it as one
anonymous account. Join `dim_user` for display name, auth provider, and signup date.
{% enddocs %}

{% docs run_status %}
Lifecycle state of the run: `pending` (queued), `running`, `success`, `error`, `timeout`, or
`interrupted` (paused at a human-in-the-loop checkpoint such as shell-command approval — the resume
arrives as a *new* run on the same thread, so interrupted runs are not failures). Reliability
questions should use `success` vs `error`/`timeout`; in the data so far the mix is ~97% success.
{% enddocs %}

{% docs thread_status %}
Current state snapshot of the conversation, not a history: `idle` (no run in flight — the normal
resting state), `busy` (a run is executing), `interrupted` (waiting on a human-in-the-loop
decision), `error` (last run failed). Do not sum statuses over time; use `fct_run.status` for that.
{% enddocs %}

{% docs message_type %}
Conversation role of the message: `human` = a user turn (what the user typed or uploaded),
`ai` = a model turn — either a final answer or, far more often, a request to call one or more
tools (`tool_call_count > 0`), `tool` = the result a tool returned to the model, `system` = injected
instructions (rare). Tool results outnumber user turns roughly 5:1, so "messages" is a poor proxy
for user activity — count `human` messages, runs, or threads instead.
{% enddocs %}

{% docs model_name %}
The LLM that produced an `ai` message, as reported by the provider (e.g. `gpt-5.4`,
`gpt-5.6-terra`); NULL for `human`/`tool` messages. This is the ground truth for "which model did
we use". The two models seen so far reflect the deployment default changing over time (gpt-5.4
until mid-July 2026, gpt-5.6-terra since) — not a main-agent vs subagent split; no run has set a
per-run override yet (`fct_run.*_model_override` are NULL).
{% enddocs %}

{% docs usage_coverage %}
Token counts come from LangChain `usage_metadata`, present on every AI message so far (814 of
814 at the 2026-09-03 rebuild). Messages persisted before 2026-09-03 carried it misfiled under
`additional_kwargs` (a since-fixed lossy coercion in the agent server); the extraction reads both
locations, so history is complete. Treat `usage_coverage` as a health check — it should stay
at ~100%, and a drop means a provider or streaming path stopped reporting usage.
{% enddocs %}

{% docs first_seen %}
When the analytics pipeline first captured the message. Source messages carry no timestamp, so
this is the event-time proxy: accurate to the hourly schedule for messages created after the
pipeline went live (2026-09-02), while every earlier message is stamped with the first backfill
run. For history before that date bucket by `thread_created_date` instead.
{% enddocs %}

{% docs est_cost %}
Estimated LLM spend in USD for the message: `input_tokens × input price + output_tokens × output
price` using the per-1M-token list prices in the `model_prices` seed (longest matching pattern
wins; cache-discounted and reasoning tokens are billed at the plain input/output rate). NULL when
the message has no usage data (see usage coverage) or the model is missing from the seed. An
estimate at list prices — not an invoice.
{% enddocs %}

{% docs steps %}
Number of LangGraph checkpoint steps recorded for the run (`max(step) + 1` across the main graph
and any subagent subgraphs). Each step is one graph super-step — roughly one model call plus its
tool batch — so it is a proxy for agent effort: quick chat replies take a handful of steps,
full resume-tailoring runs run past 100. `0` means checkpoint telemetry was missing for the run.
{% enddocs %}

{% docs duration_s %}
Seconds from run creation to its last status update; only populated for terminal statuses
(`success`, `error`, `timeout`, `interrupted`), NULL while pending/running. It includes queue wait
and any time spent waiting for a human-in-the-loop decision, so it is end-to-end latency as the
user experiences it, not model latency. Median ~6 s, p90 ~3 min, max ~5 min so far.
{% enddocs %}

{% docs tool_vocabulary %}
Tools the career agent can call, as they appear in `tool_call_names` / tool-message `name`:
`write_todos` (plan management), `task` (delegates to a subagent — the single best marker of
"heavy" multi-agent work), `read_file`/`list_files`/`ls`/`write_file`/`overwrite_file`/`edit_file`
(workspace files), `parse_document` (CV/JD parsing), `extract_jd` (job-description URL), `execute`
(shell, human-approval gated), `render_battlecard_pdf` (artifact rendering).
{% enddocs %}
