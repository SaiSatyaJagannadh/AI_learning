# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A teaching implementation of an agent harness for debugging production logs. The
`while stop_reason == "tool_use"` loop is written out explicitly rather than hidden
behind a framework, because the loop *is* the lesson. Clarity beats cleverness here:
when changing code, keep it readable by someone learning the pattern.

Three layers sit on top of each other, and each is documented at length in its own
`COMPLETE_SETUP_*.md`:

- **harness** (`logagent/`) — the loop, tools, and clients
- **skills** (`skills/`, `logagent/skills.py`) — markdown playbooks the agent loads on demand
- **evals** (`evals/`) — a three-layer regression suite over the other two

## Commands

```bash
pip install -r requirements.txt          # openai, python-dotenv, pytest
python scripts/generate_sample_logs.py   # writes 4 files into ./logs (gitignored)
```

The log generator must run before the CLI or the tool-layer evals; `./logs` is
excluded by `[Ll]ogs/` in the inherited Visual Studio `.gitignore`, so a fresh
clone has no logs until you generate them.

### Tests

```bash
python -m pytest tests/ -q                                   # 14 tests, no key, no network
python -m pytest tests/test_skills.py -q                     # one file
python -m pytest tests/test_harness.py::test_name -q         # one test
```

### Evals

```bash
python -m evals.runner --mock --skills-root ./skills   # scripted gate: tool + trajectory
python -m evals.runner --nvidia --out ./eval_out       # adds the live outcome layer
python -m evals.runner --list-cases
python -m evals.runner --list-scenarios
python -m evals.runner --layer tool                    # also: --case ID, --scenario NAME
```

Exits non-zero on failure. Under `--mock` the five outcome cases are skipped and
said so explicitly — 8/8 passing there does **not** mean the agent solved anything.

### Running the agent

```bash
python cli.py --initial-prompt "Why are we seeing 502s?" --mock
python cli.py --initial-prompt "Why are we seeing 502s?" --nvidia
```

Other flags: `--log-root` (default `./logs`), `--model`, `--api-key`,
`--max-turns`, `--max-tool-calls`, `--max-output-tokens`, `--skills-root`
(`''` disables skills), `--no-transcript`.

NVIDIA needs `NVIDIA_API_KEY` in `.env`. Default model `meta/llama-3.3-70b-instruct`;
`NvidiaClient` is the only fully working live client and reaches the API through the
`openai` SDK.

## Architecture

### The loop (`logagent/harness.py`)

`AgentHarness.run()` — defaults `max_turns=10`, `max_tool_calls=20`,
`max_output_tokens=5000`. Per turn: get a completion, append the assistant's content
list **verbatim** (preserving thinking blocks), and if `stop_reason == "tool_use"`,
execute every call and append *all* results as a **single** user message. Splitting
results across messages is accepted by the API but teaches models to stop batching.

Messages use Anthropic-style content blocks even when talking to NVIDIA —
`NvidiaClient` translates at the boundary:

```python
{"role": "user"|"assistant", "content": [{"type": "text"|"tool_use"|"tool_result", ...}]}
```

Tools return `{"content": [{"type": "text", "text": ...}], "is_error": bool}`.

### Clients (`logagent/llm.py`)

Three subclasses of `LLMClient`, in very different states:

- **`NvidiaClient`** — complete, with function calling. The production path.
- **`MockClient`** — takes a `plan` of steps; `input_adjust(prev_result)` callbacks let
  a step derive arguments from the previous tool result. Feed results back with
  `set_last_tool_result()`.
- **`ClaudeClient`** — **a stub that raises `NotImplementedError`.** Traps if you pick
  it up: `anthropic` is absent from `requirements.txt`; `__init__` raises unless an API
  key is set; the default model ID is stale; there are ~90 lines of leftover
  stream-of-consciousness left in a docstring; and `complete` is defined **twice**, so
  the second definition silently shadows the first. Rewrite it rather than patch it.

**`--mock` on the CLI passes `MockClient(plan=[])`** (`cli.py:238`). An empty plan
returns a final answer immediately, so the command above always ends in one turn with
zero tool calls and "No issues found." That is expected, not a bug — the plan-driven
mock only gets a real plan in `tests/` and `evals/`. Use the evals, not the CLI, to
exercise tool use without a key.

### Tools (`logagent/tools.py`, `logagent/logtools.py`)

`registry.register_tool(name, description, parameters, function, dangerous=False)`.
`LogTools(log_root, output_char_budget=500)` holds the five log tools; `cli.py`
registers them and sets the schemas.

### Skills (`logagent/skills.py`, `skills/*/SKILL.md`)

Skills are **tools, not prompt injection**. `register_skill_tools()` adds `list_skills`
and `load_skill`, so the agent spends tokens on a playbook only when it chooses to.
Each `SKILL.md` is YAML frontmatter (`name`, `description`, `tools`) plus a markdown
body. Four exist, keyed to incident shapes: cascading-failure, resource-exhaustion,
expiry-and-config, precision-check.

### Evals (`evals/`)

A pyramid, in `evals/case.py` as `LAYERS = ("tool", "trajectory", "outcome")`:

| Layer | Asks | Client |
|---|---|---|
| `tool` | does one tool return the right thing for given args? | none — direct call |
| `trajectory` | did the agent call the right tools in a sane order? | scripted replay |
| `outcome` | did it actually find the root cause? | live model only |

`evals/scenarios.py` generates five synthetic incidents into temp log dirs —
`cascading_failure`, `disk_full`, `memory_leak`, `cert_expiry`, and `red_herring`
(where the loudest log is a decoy). `scripts/generate_sample_logs.py` only produces
`cascading_failure`, into `./logs`.

Tool-layer cases cover `list_logs`, `read_log`, `search_logs` and `timeline`; `log_stats` has no case yet.

## Tool design principles

These are the patterns the project exists to demonstrate — preserve them in new tools.

1. **Clamp output, and say so.** `_clamp_output` truncates to a char budget and the
   tool states what it withheld ("558 matches, showing 12, 546 not shown — narrow your
   pattern"). Silent truncation makes a model reason confidently over a file it half saw.
2. **Errors are results, not exceptions.** Return `is_error: True` with a message
   written *for the model*: what went wrong **and** what to try next.
3. **Compute for the model.** `timeline` merges and sorts across files because
   correlating timestamps by hand is what a model is worst at and Python is best at.
4. **Confine paths.** `_resolve_path` keeps every access inside `log_root`.
5. **Compose.** `search_logs` finds a timestamp; `timeline` explains it.

## Dangerous tools

Tools registered `dangerous=True` require the harness's approval hook before running.
A refusal becomes an error result the model can read and adapt to — never an exception.
No current log tool is dangerous; the mechanism exists so adding a destructive tool
doesn't require retrofitting permissions.

## Documentation map

`README.md` is the tutorial and the place to start. `GETTING_STARTED.md` walks a first
run. `usefullearn.md` is the long-form concept guide. The three `COMPLETE_SETUP_*.md`
files (36–58 KB) are exhaustive per-layer references — consult the one matching the
layer you're changing. `BUILD_PROMPT.md` is the prompt that regenerates the project.
`SESSION_SUMMARY.md` is a historical log, not current state; prefer the code over it
when the two disagree.
