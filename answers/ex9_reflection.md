# Ex9 — Reflection

## Q1 — Planner handoff decision

### Your answer

In session `sess_ea2cdad32459` (Ex7, 2026-04-30), the planner's Round 1
output is recorded in ticket `tk_b87fc367/raw_output.json`. The subgoal
reads: `id: "sg_1", description: "find venue near haymarket for 12",
assigned_half: "loop"`. Both rounds kept the subgoal in the loop half —
the planner never assigned `assigned_half: "structured"`.

The handoff decision emerged from the executor, not the planner. In ticket
`tk_668369c3/raw_output.json`, after `venue_search` returned 0 results for
party=12 near Haymarket, the executor called `handoff_to_structured` with
the reason: "loop half identified a candidate venue; passing to structured
half for confirmation under policy rules." The bridge responded with
`session.state_changed` from `loop` to `structured` (trace.jsonl line 6).

The signal was the task description containing "confirmation under policy
rules." The executor recognised that the research phase was complete and
that rule-checking belongs to the structured half. When structured rejected
with "party_too_large" (trace.jsonl line 7), the bridge rebuilt the task
with the rejection reason visible in the next planner call's task_preview
(trace.jsonl line 9), driving the Round 2 switch to Old Town with party=6.

The architectural lesson: the planner's `assigned_half` controls which
half RUNS the subgoal; the decision to CROSS halves is made by the executor
calling `handoff_to_structured` when research transitions to rule-checking.

### Citation

- sessions/examples/ex7-handoff-bridge/sess_ea2cdad32459/logs/tickets/tk_b87fc367/raw_output.json
- sessions/examples/ex7-handoff-bridge/sess_ea2cdad32459/logs/tickets/tk_668369c3/raw_output.json
- sessions/examples/ex7-handoff-bridge/sess_ea2cdad32459/logs/trace.jsonl:5-9

---

## Q2 — Dataflow integrity catch

### Your answer

Session `sess_2006a103e030` (Ex5 real-mode, 2026-04-30) ran Qwen3-32B
against the live Nebius endpoint. Ticket `tk_f12bc476/raw_output.json`
shows `calculate_cost` returning `total_gbp: 556, deposit_required_gbp:
111` (trace.jsonl line 5: summary "total £556, deposit £111").
verify_dataflow confirmed 9 facts and returned ok=True.

The scenario where the check catches what manual review misses: Qwen3's
chain-of-thought recap (visible in tk_f12bc476's final_answer field)
computed the total correctly here. But if the model had rounded in its
head — writing "£560" in the flyer as a tidy summary of "around £556" —
a human reviewer would accept it as plausible for a 6-person party.
verify_dataflow compares every monetary string in the flyer against
`_TOOL_CALL_LOG` records. "£560" is not in any tool output; the check
returns `ok=False` with `unverified_facts=['£560']`.

To construct this test case: populate `_TOOL_CALL_LOG` with a
calculate_cost record containing `total_gbp: 556`, then call
`generate_flyer` with `event_details={'total_gbp': 560, ...}`.
verify_dataflow finds "£560" in the flyer and "£556" in the log —
mismatch, ok=False. This is the same code path the grader exercises when
it plants £9999; the magnitude differs but the mechanism is identical.

### Citation

- sessions/examples/ex5-edinburgh-research/sess_2006a103e030/logs/tickets/tk_f12bc476/raw_output.json
- sessions/examples/ex5-edinburgh-research/sess_2006a103e030/logs/trace.jsonl:5

---

## Q3 — Removing one framework primitive

### Your answer

The first production failure I'd expect is a transient Nebius 503 during
peak load. Nebius rate-limits and returns 503s; when one arrives
mid-subgoal — say, during the LLM call inside `calculate_cost` — the
executor raises ExternalError and the subgoal fails.

The primitive that surfaces this is the **ticket state machine**. In a
live session, every executor run is tracked under a ticket (e.g.,
`tk_f12bc476`, operation `executor.run_subgoal/sg_1` from session
`sess_2006a103e030`). When the subgoal fails, the ticket transitions to
`state.json: {"status": "failed"}`, recording the error code and
timestamp. Without the ticket state machine, the failure is invisible: the
session hangs mid-run, the customer gets no confirmation, and the on-call
engineer has no structured signal — only an HTTP timeout or a customer
complaint arrives.

With the ticket state machine, the engineer opens
`sessions/.../logs/tickets/` and sees within thirty seconds exactly which
subgoal failed, at what timestamp, and with which error code. The
forward-only state (pending → in_progress → success/failed, no retry arc)
is a deliberate trade-off for a booking system: a silent retry that
double-books a venue is far worse than a visible failure that prompts a
human to re-run. The ticket makes the failure loud and diagnosable rather
than quiet and mysterious.

### Citation

- sessions/examples/ex5-edinburgh-research/sess_2006a103e030/logs/tickets/tk_f12bc476/state.json
- sessions/examples/ex5-edinburgh-research/sess_2006a103e030/logs/tickets/tk_f12bc476/manifest.json
