# The prompt that rebuilds this project

Paste the block below into Claude Code (or any coding agent) in an empty
directory. It encodes the design decisions that matter — without them you get a
toy loop that reads whole files into context and falls over on the first big log.

Read `README.md` first if you want to know *why* each requirement is there.

---

Build me a minimal agent harness in Python that I can learn from. The agent's
job is debugging production logs.

Teaching goals come first: I want to understand what an agent harness is by
reading the code, so favour clarity over cleverness, and write comments that
explain *why* a piece exists, not what the line does.

## Structure

- `logagent/harness.py` — the agent loop
- `logagent/tools.py` — tool schema/registry/execution plumbing
- `logagent/logtools.py` — the log tools themselves
- `logagent/llm.py` — the model client
- `logagent/transcript.py` — tracing
- `cli.py` — entry point
- `scripts/generate_sample_logs.py` — makes the sample data
- `tests/test_harness.py`
- `README.md` — a tutorial, not just usage notes

## Write the loop by hand

Do NOT use `tool_runner`, LangChain, or any agent framework. The manual
`while stop_reason == "tool_use"` loop is the thing I'm trying to learn. Mention
the production alternatives in the README at the end, once I've seen the real thing.

## The loop must have brakes

- caps on model turns, total tool calls, and cumulative output tokens
- a `dangerous` flag on tools plus an `approve()` hook the harness calls before
  running one; a refusal becomes a "user declined" tool result, not an exception
- append the assistant's content list **verbatim** to messages (so thinking
  blocks survive the round trip) — add a test pinning this
- when one turn contains several tool calls, all results go back in a **single**
  user message — add a test pinning this too
- handle `pause_turn`

## Tool rules (this is the important part)

- Every tool clamps its own output to a char budget AND says what it held back
  ("558 matches, showing 12, 546 not shown — narrow your pattern"). Silent
  truncation is a bug: the model will reason confidently about a file it half saw.
- Tool failures return to the model as results with `is_error: true`, never as
  raised exceptions. Write those error strings *for the model*: what went wrong
  AND what to try instead.
- Every file path is resolved and confined to a log root, so `../../etc/passwd`
  gets a clean error.
- No "read the whole file" tool. Reading is paginated by line range, on purpose.

## The five tools

`list_logs`, `search_logs` (regex, context lines, capped), `read_log`
(paginated), `log_stats` (severity counts + an errors-over-time histogram), and
`timeline` — merge several files into one chronological view around a timestamp.

`timeline` is the point: correlating four files by hand is what a model is worst
at and Python is best at. Anything you can compute *for* the model — counts,
sorts, joins — compute for the model.

Spend real effort on the tool `description` fields. They're the only thing the
model reads when deciding what to call.

## It must run with no API key

Ship a scripted `MockClient` next to the real Claude client, behind one
`complete()` method so the loop doesn't know which is which. The mock follows a
fixed plan but reads the previous tool result to pick its next arguments — I want
to see that turn N+1 depends on what turn N observed. Every test must pass with
no key and no network.

For the real client use the current Anthropic Python SDK with streaming and
adaptive thinking. Look up the current model ID rather than recalling one.

## Sample data

Generate ~3000 lines across four log files describing ONE cascading failure
where the root cause is only findable by correlating all four — e.g. a deploy
changes a config value, the database starts refusing connections, a service times
out, the gateway returns 502s. Fixed random seed so it's reproducible. Check
whether `.gitignore` excludes `*.log` before committing them.

## Transcript

Print every turn, every tool call with its arguments, and a preview of every
result. An agent that prints only its final answer can't be debugged — I need to
see whether a wrong answer came from bad reasoning or a bad tool result.

## README

Write it as a tutorial: what a harness is, the loop in ~9 lines, a run
walkthrough, the things that turn a loop into a harness, why tool design beats
tool count, an exercise adding a tool, and an honest section on when to use the
SDK's tool runner instead.

Finally: run the tests, run the CLI end to end from a clean state, and show me
the output.
