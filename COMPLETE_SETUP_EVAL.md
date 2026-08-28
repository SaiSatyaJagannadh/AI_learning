# Complete Evaluation Suite Setup & Architecture
**Complete System Documentation - Everything Connected**

## Table of Contents
1. [System Overview](#system-overview)
2. [Complete Architecture](#complete-architecture)
3. [File-by-File Breakdown](#file-by-file-breakdown)
4. [The Four Layers](#the-four-layers)
5. [Data Flow](#data-flow)
6. [How Everything Connects](#how-everything-connects)
7. [Running Evaluations](#running-evaluations)
8. [Adding New Evaluations](#adding-new-evaluations)
9. [Production Implementation Guide](#production-implementation-guide)
10. [Troubleshooting](#troubleshooting)

---

## System Overview

This is the **measurement half** of the project. `COMPLETE_SETUP_HARNESS.md` describes
an agent that investigates logs; this suite answers the only question that matters
about it: **does it actually work, and did the last change make it better or worse?**

The suite:
1. Generates deterministic log fixtures for five distinct failure modes
2. Puts scored questions to the agent across four layers
3. Grades every run with deterministic graders (plus one optional LLM judge)
4. Aggregates into a console table, a markdown report, and a diffable JSON file
5. Exits non-zero on failure, so it works as a CI gate

**Key Innovation:** the layers differ in *what they hold fixed*, not in how they score.
Fix everything (tool layer) and you are testing code. Fix the model's choices
(trajectory layer) and you are testing the loop. Fix nothing (outcome layer) and you
are testing the agent. A failure at the bottom explains every failure above it, which
is what makes a red run debuggable instead of demoralising.

---

## Complete Architecture

### The Big Picture

```
┌─────────────────────────────────────────────────────────────────┐
│                  python -m evals.runner --mock                   │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    _main()  (bottom of runner.py)                │
│  • Parse flags (--mock/--nvidia, --layer, --case, --skills-root) │
│  • Load .env (NVIDIA_API_KEY) exactly as cli.py does            │
│  • Filter ALL_CASES down to the selected cases                  │
│  • Build the two injected factories:                            │
│      client_factory(case)      -> ScriptedClient | NvidiaClient  │
│      scenario_dir_factory(name)-> a directory of fixture logs    │
│  • Construct EvalRunner, call run_suite(cases)                  │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    EvalRunner.run_case(case)                     │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  1. result.ground_truth = _lookup_ground_truth(scenario)  │  │
│  │  2. log_root = scenario_dir_factory(case.scenario)        │  │
│  │       └─► scenarios.materialize() writes the fixture logs │  │
│  │  3. registry = build_registry(LogTools(log_root))         │  │
│  │       └─► the same 5 tools cli.py registers, verbatim     │  │
│  │  4. _instrument(registry) - wrap execute_tool             │  │
│  │  5. layer == "tool":  call the tool directly, no LLM      │  │
│  │     otherwise:        AgentHarness(...).run(case.prompt)  │  │
│  │  6. result.scores = [g.grade(result, case) for g in ...]  │  │
│  └───────────────────────────────────────────────────────────┘  │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    SuiteReport  ->  report.py                    │
│  render_console()  -> the table you read in the terminal        │
│  render_markdown() -> report.md, for a PR comment               │
│  render_json()     -> results.json, for `git diff` across runs  │
└─────────────────────────────────────────────────────────────────┘
```

### The instrumentation seam

The suite needs an ordered record of every tool the agent called. The obvious place
to get it is inside the harness — and that is exactly why it is not done there:

```python
# evals/runner.py - EvalRunner._instrument
original_execute_tool = registry.execute_tool

def execute_and_record(name, tool_input):
    tool_result = original_execute_tool(name, tool_input)
    result.tool_calls.append({
        "name": name,
        "input": tool_input,
        "is_error": bool((tool_result or {}).get("is_error", False)),
        "output": _text_of(tool_result),
    })
    if callable(feedback):          # client.set_last_tool_result
        try:
            feedback(tool_result)
        except Exception:
            pass                    # bookkeeping bug != tool failure
    return tool_result

registry.execute_tool = execute_and_record
```

**Instrument the registry, not the harness.** Editing `harness.py` to serve the eval
suite would mean the suite no longer measures the harness people actually ship. The
wrapper does double duty: the record it appends is what every trajectory grader reads,
and the `set_last_tool_result` callback is what lets a scripted client's *callable*
inputs react to real tool output.

---

## File-by-File Breakdown

### Core Files

#### `evals/case.py` — the vocabulary (222 lines, zero dependencies)

Defines the five types every other module speaks. **It imports nothing from the
harness**, which is what lets graders be unit-tested against hand-built results with
no agent, no tools and no LLM in the loop.

| Type | What it is |
|---|---|
| `GroundTruth` | The answer key for a scenario |
| `EvalCase` | One scored question, plus the graders that score it |
| `GraderScore` | One grader's verdict on one run |
| `EvalResult` | Everything observable about a single run |
| `SuiteReport` | Aggregate over many results |

```python
LAYERS = ("tool", "trajectory", "outcome")   # "skill" cases run at the tool layer

@dataclass
class GroundTruth:
    root_cause_keywords: List[str]   # all must appear (case-insensitive)
    forbidden_keywords:  List[str]   # none may appear - this is the precision score
    expected_tools:      List[str]   # must be called at least once
    culprit_file: str = ""           # for tool-layer assertions
    culprit_timestamp: str = ""
    description: str = ""            # reference answer for the LLM judge
```

The answer key is keywords and tool names, **not an expected string**. There are many
correct ways to phrase "the deploy raised DB_POOL_SIZE past what the database allows",
and an eval that demands one phrasing measures wording, not understanding.

Three details that carry weight:

```python
def __post_init__(self):        # EvalCase - fail at import, not at run time
    if self.layer not in LAYERS: raise ValueError(...)
    if self.layer == "tool" and not self.tool_name: raise ValueError(...)

def __post_init__(self):        # GraderScore - clamp rather than raise
    self.score = max(0.0, min(1.0, float(self.score)))

@property
def passed(self) -> bool:       # EvalResult
    if self.error is not None: return False
    if not self.scores: return False        # a case with no graders never passes
    return all(s.passed for s in self.scores)
```

That middle line — `if not self.scores: return False` — is why every case in
`cases.py` must carry at least one grader. An ungraded case is not a passing case;
it is a case that is not testing anything.

Per-case brakes default to `max_turns=8, max_tool_calls=12, max_output_tokens=4000` —
deliberately tighter than the harness's 10/20/5000. An eval that lets the agent wander
for ten turns is measuring patience, not skill.

#### `evals/graders.py` — the scoring (9 graders + 1 bundle)

The entire contract is three lines:

```python
class MyGrader(Grader):
    name = "my_grader"
    def grade(self, result: EvalResult, case: EvalCase) -> GraderScore: ...
```

| Grader | `name` | Scores |
|---|---|---|
| `KeywordGrader` | `keywords` | Every required keyword present. Partial credit = fraction found; `passed` requires all |
| `ForbiddenKeywordGrader` | `forbidden` | No decoy keyword present — this is the precision metric |
| `RegexGrader` | `regex` | Final answer must (or must not) match a pattern |
| `ToolTrajectoryGrader` | `tool_trajectory` | Every expected tool called at least once |
| `ToolOrderGrader` | `tool_order` | Expected tools appear in the expected relative order |
| `NoToolErrorGrader` | `no_tool_error` | No tool returned `is_error: True` |
| `BrakeGrader` | `brakes` | Stayed inside the brakes **and** stopped on its own |
| `ToolOutputGrader` | `tool_output` | Direct assertions on one tool's raw output |
| `LLMJudgeGrader` | `llm_judge` | Whether the *causal story* matches the reference |

`standard_outcome_graders()` returns the default bundle: `KeywordGrader`,
`ForbiddenKeywordGrader`, `ToolTrajectoryGrader`, `BrakeGrader` — did it find the
cause, avoid the decoy, actually use tools, and finish under its own steam?

Three rules the graders follow, each of which is load-bearing:

1. **Never raise on a bad answer.** A wrong answer is `0.0`, not an exception.
   Exceptions are reserved for a *broken grader*, and the runner catches those
   separately so one bad grader cannot take down the suite.
2. **Partial credit is a feature.** "Found 3 of 4 required facts" (0.75) tells you far
   more about a regression than a bare `False`.
3. **The optional grader abstains, it does not fail.** With no `NVIDIA_API_KEY`,
   `LLMJudgeGrader` returns `passed=True` with the note `abstained (no
   NVIDIA_API_KEY; judge is optional)`. A missing optional grader must never turn CI
   red. It also abstains on a judge outage and on an unparseable verdict.

`BrakeGrader` is subtler than it looks — it separates three outcomes, not two:

```python
if over_turns or over_calls:                       # breached a brake
    return self._score(False, 0.0, "brake breached: ...")
if self.require_natural_stop and result.turns >= case.max_turns:
    return self._score(False, 0.5, "ran to the turn limit instead of concluding")
return self._score(True, 1.0, f"{result.turns}/{case.max_turns} turns, ...")
```

Hitting `max_turns` is not a crash, but it *is* a failure of the thing being measured:
the agent did not reach a conclusion on its own. Half credit records that it was close.

`LLMJudgeGrader` exists for the one thing keywords genuinely cannot score: an answer
can contain every required keyword and still get the direction of causation backwards
("the 502s caused the deploy"). It asks for a strict two-line reply so the suite parses
a decision rather than a vibe:

```
VERDICT: PASS or FAIL
REASON: one sentence
```

#### `evals/scenarios.py` — the fixtures (five failure modes)

| Scenario | Root cause | Notable |
|---|---|---|
| `cascading_failure` | Deploy raises `DB_POOL_SIZE` 10→100 against `max_connections=50` | The cause is logged at **INFO** two minutes before the ERROR storm |
| `disk_full` | `/var/lib/data` reaches 100%, everything downstream is `ENOSPC` | Tests finding the *crossing point*, not the plateau |
| `memory_leak` | `worker-3` RSS climbs to the 4096 MB cgroup limit, OOM killed, restarted into the same leak | Tests recognising a restart loop as a leak |
| `cert_expiry` | TLS cert for `CN=api.internal` passes its `notAfter` mid-window | A WARN announces it a minute early |
| `red_herring` | Thread pool saturates (32/32); a loud cache-miss spike is the decoy | The only scenario with `forbidden_keywords` — this is the precision test |

Every generator is a pure function of a fixed anchor timestamp:

```python
def _ts(offset_s: int) -> str:    # log-line form:      2026-08-24T10:03:00
def _at(offset_s: int) -> str:    # ground-truth form:  2026-08-24 10:03:00
```

Two timestamp forms on purpose. The real sample logs emit ISO with a `T`; `timeline`
accepts a space. `TIMESTAMP_RE` in `logtools.py` matches `[ T]` for exactly this
reason — matching only one form silently drops every line of the other kind, making
`timeline` and `log_stats` look *empty* rather than *broken*. The `tool-timeline`
case exists to catch precisely that regression.

Each log file is written with a leading `# name` comment line that has **no
timestamp**, matching the real sample logs. A tool that chokes on an unparseable line
should fail in the fixture, not in production.

Public API:

```python
SCENARIOS: Dict[str, Scenario]           # the registry
get_scenario(name) -> Scenario           # raises KeyError naming the valid ones
materialize(name, dest_dir) -> str       # writes the logs, returns the dir
list_scenarios() -> List[Scenario]       # registry order
iso_form(timestamp) -> str               # space-separated -> log-line form
```

#### `evals/cases.py` — the questions (13 cases)

`make_case()` is the helper; the only subtlety is grader defaulting:

```python
def make_case(case_id, layer, scenario_name, prompt, ..., graders: list = None):
    scenario = get_scenario(scenario_name)          # unknown name fails at import
    case = EvalCase(..., graders=list(graders or []))

    # Explicit graders are kept as-is; the standard outcome bundle is only
    # auto-added when none were supplied, so a case can always opt out.
    if layer == "outcome" and not case.graders:
        case.graders.append(KeywordGrader(keywords=scenario.ground_truth.root_cause_keywords))
        if scenario.ground_truth.forbidden_keywords:
            case.graders.append(ForbiddenKeywordGrader(keywords=...))
        case.graders.append(ToolTrajectoryGrader(expected=scenario.ground_truth.expected_tools))
        case.graders.append(BrakeGrader())
    return case
```

The case lists:

```python
TOOL_CASES       # 4: list_logs, read_log, search_logs, timeline
SKILL_CASES      # 3: list_skills, load_skill, load_skill-unknown  (need --skills-root)
TRAJECTORY_CASES # 1: traj-cascading_failure_tool_usage
OUTCOME_CASES    # 5: one per scenario
ALL_CASES = TOOL_CASES + SKILL_CASES + TRAJECTORY_CASES + OUTCOME_CASES
```

#### `evals/replay.py` — the fake model

```python
class ScriptedClient(LLMClient):
    def __init__(self, script: List[Dict[str, Any]], final_text: str = "Investigation complete.")
    def set_last_tool_result(self, result) -> None    # the harness feeds results back here
```

One dict per turn. `{"tools": [{"name": ..., "input": ...}]}` returns `tool_use`;
`{"text": ...}` returns `stop`; `{"pause": True}` returns `pause_turn` so the harness's
pause handling is exercised. Running off the end of the script returns `final_text`.

A tool `input` may be a **callable** taking the previous tool's output text. A tool
that starts returning nothing then breaks the script loudly instead of silently
replaying stale arguments. A callable that raises is surfaced as
`{"__script_error__": ...}` — an argument the real tool will reject — rather than
falling back to a default that happens to work.

`client.calls` records every request the harness made, so tests can assert on message
shape and verbatim content, not just on the final answer.

```python
class AnswerOnlyClient(ScriptedClient):
    """The negative control: a word-perfect answer with zero tool calls."""
```

Point it at an outcome case and `KeywordGrader` will happily pass it. That is the
demonstration that keyword graders alone are not enough, and why every outcome case
also carries a `ToolTrajectoryGrader`.

#### `evals/runner.py` — the execution engine

```python
build_registry(log_tools, skills_root=None) -> ToolRegistry
```

Registers the five log tools **with names, descriptions and JSON Schemas copied
verbatim from cli.py**. A model's behaviour is a function of its tool descriptions as
much as of its prompt, so an eval that registered a "cleaned up" schema would be
measuring a harness nobody runs. If `cli.py`'s wording changes, this must change with
it — that coupling is the point, not an accident. Passing `skills_root` adds
`list_skills` / `load_skill` through the same `register_skill_tools()` call the CLI
makes (see `COMPLETE_SETUP_SKILL.md`).

```python
class EvalRunner:
    def __init__(self, client_factory, scenario_dir_factory,
                 verbose=False, output_char_budget=500, skills_root=None)
    def run_case(self, case: EvalCase) -> EvalResult
    def run_suite(self, cases: List[EvalCase]) -> SuiteReport
```

Both factories are **injected** rather than hardcoded, which is what lets the same
runner drive the scripted regression gate and a live-model measurement run: swap the
client factory and nothing else changes. The scenario directory factory is injected
for the same reason — fixtures go into a `tmp_path` under pytest and into a persistent
directory from the CLI.

`output_char_budget=500` is not a detail, it is part of what the suite tests: an agent
that only ever sees clamped tool output has to *compose* tools instead of slurping
whole files.

Everything is contained:

```python
except Exception as e:
    result.error = _short_traceback(e)
finally:
    result.duration_s = time.perf_counter() - started

# Graded even on error: EvalResult.passed already forces False when error is
# set, and the grader details explain what the run managed to do first.
result.scores = self._grade(result, case)
```

A grader that raises becomes a failed `GraderScore` naming the traceback, not an
exception that kills the suite. A run that blows up sets `result.error` and is still
graded, so **a broken harness stays visually distinct from a wrong answer**. An eval
suite whose own failures look like scenario failures is worse than no suite at all.

Ground truth is looked up lazily (`_lookup_ground_truth`) so `runner.py` and
`scenarios.py` never form an import cycle.

#### `evals/report.py` — the output

```python
render_console(report, verbose=False) -> str   # the terminal table
render_markdown(report) -> str                 # report.md, for a PR comment
render_json(report) -> str                     # results.json, for git diff
write_reports(report, out_dir) -> Dict[str, str]   # writes both, returns paths
```

Filenames are fixed, not timestamped: the whole point of the JSON report is
`git diff` across commits, and a name that changes every run makes that impossible.

On failure the console output prints the failing grader details **and the reference
answer**, so a human reading a red test knows what was supposed to happen without
opening `scenarios.py`.

### Supporting Files

| File | Purpose |
|---|---|
| `evals/__init__.py` | Empty; makes `python -m evals.runner` work |
| `evals/README.md` | Short orientation; this document is the full reference |
| `tests/test_harness.py` | Harness unit tests — the seam the runner reuses |
| `tests/test_skills.py` | Skill-layer unit tests |

---

## The Four Layers

Cases are grouped by **what is held fixed**, which is also the order to debug them in.

### 1. Tool Layer — nothing is variable
- **LLM involvement:** none at all. The case *is* the tool call.
- **Graded by:** `ToolOutputGrader`
- **Catches:** the timestamp-parsing class of bug, where `timeline` returns an empty
  result against the project's own logs and an outcome eval reports it only as a vague
  "the agent seemed confused".
- **Cost:** free, milliseconds. Run these constantly.

The registry is instrumented for tool cases too, even though there is no agent, so a
tool-layer failure reads the same way as every other layer.

### 2. Skill Layer — the playbooks, scored as tools
- **LLM involvement:** none.
- **Graded by:** `ToolOutputGrader`, including one `expect_error=True` negative control.
- **Catches:** a playbook that fails to parse, a skill index that omits a skill, an
  unknown-skill error that does not name the alternatives.
- **Requires:** `--skills-root ./skills`. Without it the skill tools are not
  registered and the cases are skipped with a message rather than failing on
  "unknown tool" — a wiring problem dressed up as a test failure helps nobody.

### 3. Trajectory Layer — the model's choices are fixed
- **LLM involvement:** scripted (`ScriptedClient` replays `case.script`).
- **Graded by:** `ToolTrajectoryGrader`, `BrakeGrader`
- **Catches:** loop bugs. Tool results not fed back, `pause_turn` mishandled, brakes
  off by one, multiple tool calls in a turn not collected into a single user message.
- **Cost:** free. This is the CI regression gate.

A scripted case always replays its script, **even under `--nvidia`**: a trajectory
case exists to pin the tool sequence, and handing it to a live model would be
measuring something else entirely.

### 4. Outcome Layer — nothing is fixed
- **LLM involvement:** live (`NvidiaClient`).
- **Graded by:** the standard bundle — `KeywordGrader`, `ForbiddenKeywordGrader`
  (where a decoy exists), `ToolTrajectoryGrader`, `BrakeGrader`.
- **Catches:** the thing you actually care about — can it diagnose an incident?
- **Cost:** an API call per turn per case. Run it on a release, not on a save.

Under `--mock` these are skipped with a message, because an outcome case with no
script has nothing for a scripted client to replay: it would answer with no tools and
fail by construction. **A suite that is red by default is a suite people stop reading.**

---

## Data Flow

### One outcome case, end to end

```
python -m evals.runner --nvidia --case outcome-cascading_failure
  │
  ├─ 1. SETUP
  │     materialize("cascading_failure", <tmp>/cascading_failure)
  │       └─► deploy.log, database.log, service.log, gateway.log
  │           (deterministic: same bytes every run)
  │     result.ground_truth = GroundTruth(
  │         root_cause_keywords = ["DB_POOL_SIZE", "deploy"],
  │         expected_tools      = ["list_logs", "search_logs", "timeline"])
  │     registry = build_registry(LogTools(log_root, output_char_budget=500))
  │     registry.execute_tool = execute_and_record        # instrumented
  │
  ├─ 2. EXECUTION   AgentHarness(...).run(case.prompt)
  │     prompt: "What is the root cause of the 502 errors seen in the gateway
  │              logs around 10:03-10:05?"
  │
  │     Turn 1  LLM -> tool_use  search_logs{pattern:"502", file:"gateway.log"}
  │             registry records the call; clamped result goes back as a
  │             single user message
  │     Turn 2  LLM -> tool_use  search_logs{pattern:"DB_POOL_SIZE", file:"deploy.log"}
  │     Turn 3  LLM -> tool_use  timeline{files:[...], around:"2026-08-24 10:03:00"}
  │     Turn 4  LLM -> stop      final text answer
  │
  ├─ 3. COLLECTION
  │     result.final_answer = "The 2.3.0 deploy raised DB_POOL_SIZE from 10 to 100..."
  │     result.tool_calls   = [search_logs, search_logs, timeline]  (with inputs)
  │     result.turns        = harness.turn_count       # 4
  │     result.duration_s   = 12.4
  │
  ├─ 4. GRADING
  │     keywords         1.00  both required keywords present
  │     tool_trajectory  1.00  called 3/3 expected tools
  │     brakes           1.00  4/8 turns, 3/12 calls
  │     -> EvalResult.passed = all(...) = True
  │
  └─ 5. REPORTING
        SuiteReport -> render_console / render_markdown / render_json
        exit 0
```

### What a failure looks like

The report is built so the failure explains itself:

```
LAYER: outcome  (0/1 passed)
  CASE                       STATUS  SCORE  TURNS  CALLS   TIME
  -------------------------  ------  -----  -----  -----  -----
  outcome-cascading_failure  FAIL     0.33      1      0  0.01s
      keywords: 0/2 required keywords; missing: 'DB_POOL_SIZE', 'deploy'
      tool_trajectory: called 0/3 expected tools; never called: list_logs,
        search_logs, timeline (answered without opening a single log)
      expected: At 10:03:00 the 2.3.0 deploy changed DB_POOL_SIZE from 10 to 100...
```

`ERROR` in the status column instead of `FAIL` means the *run* blew up, not the agent:

```
  outcome-cascading_failure  ERROR    0.00      0      0  0.76s
      error: AttributeError: module aiohttp has no attribute SocketTimeoutError
```

That distinction is the reason `result.error` exists.

---

## How Everything Connects

### The Connection Map

```
evals/runner.py::_main()
  │
  ├─► imports ALL_CASES ──────────► evals/cases.py
  │                                    └─► get_scenario() ─► evals/scenarios.py
  │                                    └─► graders          ─► evals/graders.py
  │
  ├─► scenario_dir_factory ───────► scenarios.materialize() ─► fixture .log files
  │      (memoized: five cases over one scenario read the same bytes)
  │
  ├─► client_factory ─────────────► ScriptedClient (evals/replay.py)
  │                                 or NvidiaClient (logagent/llm.py)
  │
  └─► EvalRunner.run_suite()
         │
         ├─► build_registry() ─────► logagent/logtools.py  (the 5 log tools)
         │                           logagent/skills.py    (if --skills-root)
         │
         ├─► _instrument() ────────► wraps registry.execute_tool
         │                           records calls + feeds the scripted client
         │
         ├─► AgentHarness.run() ───► logagent/harness.py   (the real loop)
         │                           logagent/transcript.py (verbose only)
         │
         ├─► _grade() ─────────────► every grader on the case
         │
         └─► SuiteReport ──────────► evals/report.py
```

### The Dependency Graph

```
                    ┌──────────────────┐
                    │ evals/runner.py  │
                    └────────┬─────────┘
                             │
     ┌───────────────┬───────┴───────┬────────────────┐
     ▼               ▼               ▼                ▼
┌──────────┐  ┌────────────┐  ┌────────────┐  ┌─────────────┐
│cases.py  │  │scenarios.py│  │ replay.py  │  │  report.py  │
└────┬─────┘  └─────┬──────┘  └─────┬──────┘  └──────┬──────┘
     │              │               │                │
     └──────────────┴───────┬───────┴────────────────┘
                            ▼
                     ┌─────────────┐
                     │  case.py    │   <- imports nothing from logagent
                     └─────────────┘
                            ▲
                     ┌──────┴──────┐
                     │ graders.py  │
                     └─────────────┘

runner.py additionally depends on:
  • logagent.harness   (AgentHarness - the thing under test)
  • logagent.logtools  (LogTools)
  • logagent.tools     (ToolRegistry)
  • logagent.skills    (SkillLibrary, register_skill_tools)
  • logagent.transcript(Transcript)
  • logagent.llm       (LLMClient, NvidiaClient - CLI path only)

case.py depends on:      dataclasses, typing            (stdlib only)
graders.py depends on:   case.py, abc, re               (+ logagent.llm, lazily,
                                                          only inside LLMJudgeGrader)
scenarios.py depends on: os, datetime                   (stdlib only)
replay.py depends on:    logagent.llm.LLMClient
report.py depends on:    case.py, json, os              (stdlib only)
```

The arrow that matters is the one that is **missing**: `case.py` and `graders.py`
have no path to the harness. That is what keeps grader unit tests instant and
network-free.

---

## Running Evaluations

### Prerequisites
```bash
pip install -r requirements.txt          # openai, python-dotenv, pytest
cp .env.example .env                     # then add NVIDIA_API_KEY=nvapi-...
```
`--mock` needs neither the key nor the network.

### Basic Commands

```bash
# The CI gate: tool + trajectory layers, scripted, no API key, ~20 ms
python -m evals.runner --mock

# Add the skill layer (needs the playbook directory)
python -m evals.runner --mock --skills-root ./skills

# The real measurement: live model on the outcome layer
python -m evals.runner --nvidia

# Narrow down
python -m evals.runner --layer tool
python -m evals.runner --scenario cascading_failure --nvidia
python -m evals.runner --case outcome-red_herring --nvidia

# Discover
python -m evals.runner --list-cases
python -m evals.runner --list-scenarios

# Detail and artifacts
python -m evals.runner --mock --verbose          # rationales + full transcripts
python -m evals.runner --nvidia --out ./eval_out # writes results.json + report.md
python -m evals.runner --mock --keep-logs        # fixtures land in ./eval_logs

# Pick the model
python -m evals.runner --nvidia --model meta/llama-3.1-70b-instruct
```

### Every Flag

| Flag | Effect |
|---|---|
| `--mock` | Scripted clients only. **The default** when `--nvidia` is absent, so CI never silently starts costing money |
| `--nvidia` | Live NVIDIA API for outcome cases. Scripted cases still replay their scripts |
| `--model MODEL` | Override the model id |
| `--api-key KEY` | NVIDIA key; defaults to `$NVIDIA_API_KEY` from `.env` |
| `--scenario NAME` | Only cases from this scenario |
| `--case ID` | Only this case id (also overrides both auto-skips) |
| `--layer {tool,trajectory,outcome}` | Only this layer |
| `--skills-root DIR` | Register `list_skills`/`load_skill`; required by the skill cases |
| `--verbose` | Case rationales, harness transcripts, per-case detail |
| `--list-cases` / `--list-scenarios` | Print and exit |
| `--out DIR` | Also write `results.json` and `report.md` |
| `--keep-logs` | Generate fixtures into `./eval_logs` instead of a temp dir |

**Exit code:** `0` if every case passed, `1` if any failed, `2` if the filters matched
no cases. Suitable as a CI gate as-is.

### Reading the Output

```
========================================================================
EVAL SUITE  client=scripted
========================================================================

LAYER: tool  (7/7 passed)
  CASE                     STATUS  SCORE  TURNS  CALLS   TIME
  -----------------------  ------  -----  -----  -----  -----
  tool-list_logs           PASS     1.00      0      1  0.01s
  tool-timeline            PASS     1.00      0      1  0.01s
  tool-load_skill-unknown  PASS     1.00      0      1  0.00s

LAYER: trajectory  (1/1 passed)
  traj-cascading_failure_tool_usage  PASS     1.00      4      3  0.00s

------------------------------------------------------------------------
SUMMARY  8/8 passed (100.0%)  mean score 1.00  wall 0.02s
------------------------------------------------------------------------
```

| Column | Meaning |
|---|---|
| `STATUS` | `PASS` / `FAIL` / `ERROR` — `ERROR` means the run crashed, not that the agent was wrong |
| `SCORE` | Mean of that case's grader scores (partial credit) |
| `TURNS` | `harness.turn_count` — how many LLM round trips |
| `CALLS` | Tool invocations recorded by the instrumented registry |
| `TIME` | Wall clock for the case |

---

## Adding New Evaluations

### Add a Scenario

1. Write a deterministic generator in `scenarios.py`:
   ```python
   _NEW_SCENARIO_S = 180        # seconds after the anchor; the culprit moment

   def _gen_new_scenario(dest_dir: str) -> None:
       lines = []
       for i in range(200):
           t = i * 2
           if t == _NEW_SCENARIO_S:
               lines.append(f"{_ts(t)} INFO the thing that actually broke it")
           else:
               lines.append(f"{_ts(t)} INFO business as usual")
       _write_log(dest_dir, "app.log", lines)
   ```
   No randomness, no `datetime.now()`. A fixture that varies makes a red run
   un-attributable: you cannot tell the agent regressed from the fixture moving.

2. Register it with its answer key:
   ```python
   "new_scenario": Scenario(
       name="new_scenario",
       description="One line, for --list-scenarios",
       ground_truth=GroundTruth(
           root_cause_keywords=["keyword1", "keyword2"],   # keep short and causal
           forbidden_keywords=["the_decoy"],               # omit if there is none
           expected_tools=["search_logs", "timeline"],     # the minimum to investigate
           culprit_file="app.log",
           culprit_timestamp=_at(_NEW_SCENARIO_S),
           description="Full prose diagnosis - the LLM judge's reference answer.",
       ),
       generate=_gen_new_scenario,
   ),
   ```

3. Add a matching outcome case. The graders come from the ground truth automatically.

**Put the decoy in `forbidden_keywords`.** A scenario with no wrong answer available
cannot measure precision, only recall — and recall alone is passed by an agent that
lists every anomaly it can find.

### Add a Case

```python
# Tool layer - assert on raw output
make_case(
    case_id="tool-log_stats",
    layer="tool",
    scenario_name="cascading_failure",
    prompt="",                                   # unused at this layer
    rationale="Severity counts must survive both timestamp formats",
    tool_name="log_stats",
    tool_input={"files": ["database.log"]},
    max_turns=1, max_tool_calls=1,
    graders=[ToolOutputGrader(must_contain=["ERROR", "WARN"], min_lines=3)],
)

# Trajectory layer - pin the tool sequence with a script
make_case(
    case_id="traj-disk_full_pattern",
    layer="trajectory",
    scenario_name="disk_full",
    prompt="Why did writes start failing?",
    script=[
        {"tools": [{"name": "search_logs", "input": {"pattern": "ENOSPC"}}]},
        {"tools": [{"name": "timeline", "input": {
            "files": ["storage.log"], "around": "2026-08-24 10:04:00"}}]},
        {"text": "The volume filled."},
    ],
    graders=[ToolTrajectoryGrader(expected=["search_logs", "timeline"]), BrakeGrader()],
)

# Outcome layer - graders are added for you
make_case(
    case_id="outcome-new_scenario",
    layer="outcome",
    scenario_name="new_scenario",
    prompt="What is the root cause of the errors around 10:03?",
    rationale="End-to-end diagnosis of <failure mode>",
)
```

Then add it to the right list and to `ALL_CASES`. Always write the `rationale` — a
case nobody can explain is a case nobody will maintain.

**Assert on what the clamp lets through.** Tool output is clamped to 500 characters in
the suite. An assertion on a line that falls past the clamp fails for a reason that has
nothing to do with the tool, as `tool-timeline` originally did.

### Add a Grader

```python
class MyNewGrader(Grader):
    name = "my_new_grader"        # this string is what prints in the report

    def __init__(self, threshold: float = 0.8):
        self.threshold = threshold

    def grade(self, result: EvalResult, case: EvalCase) -> GraderScore:
        hits = ...                                    # count what you care about
        score = hits / max(1, total)
        if score < self.threshold:
            return self._score(False, score, f"only {hits}/{total}: <what is missing>")
        return self._score(True, score, f"{hits}/{total}")
```

Three rules: never raise for a wrong answer; make `detail` say *what was missing* and
not merely that something was; if the grader needs the network, abstain rather than
fail when it is unavailable.

---

## Production Implementation Guide

### Where each layer belongs

| Layer | Cadence | Gate? | Cost |
|---|---|---|---|
| Tool | Every commit, pre-commit hook | Yes, hard | Free |
| Skill | Every commit that touches `skills/` | Yes, hard | Free |
| Trajectory | Every commit | Yes, hard | Free |
| Outcome | Every release, nightly | Soft — track the trend | One API call per turn per case |

### As a CI gate

```yaml
- run: pip install -r requirements.txt
- run: python -m pytest tests/ -q
- run: python -m evals.runner --mock --skills-root ./skills   # exits 1 on failure
- run: python -m evals.runner --nvidia --out ./eval_out       # nightly only
  env:
    NVIDIA_API_KEY: ${{ secrets.NVIDIA_API_KEY }}
```

Commit `eval_out/results.json`. Its fixed filename and stable field order are what let
`git diff` show a regression as a diff rather than as a memory of yesterday's terminal.

### Interpreting a live run

The outcome layer is **stochastic**. A single failure is a data point, not a
regression. Before changing anything:

1. Re-run the failing case a few times. Note the pass rate rather than the outcome.
2. Check the layer below. A trajectory failure explains an outcome failure; the
   reverse is not true.
3. Read `tool_trajectory` first. "Answered without opening a single log" is a prompt
   problem; "called 3/3 expected tools" with wrong keywords is a reasoning problem.
   They have opposite fixes.

### Resource notes

- **Time:** the scripted suite is milliseconds. Outcome cases cost one API round trip
  per turn, up to `max_turns=8`.
- **Cost:** only the outcome layer and the optional judge spend tokens.
- **Disk:** fixtures are a few hundred KB in a temp dir, removed by the OS. `--keep-logs`
  puts them in `./eval_logs` for inspection.
- **Concurrency:** cases run sequentially. `run_suite` is a list comprehension over
  `run_case`; parallelising it needs a per-case scenario directory, which the injected
  factory already supports.

### Best Practices

1. **Start at the bottom layer.** Fix tool failures before reading outcome failures.
2. **Keep `--mock` green at all times.** A suite that is red by default stops being read.
3. **Every case gets a `rationale`.** It prints in `--verbose` and it is the only
   record of why the case exists.
4. **Every scenario with an available wrong answer gets `forbidden_keywords`.**
   Recall without precision is not a measurement.
5. **Never let a case ship with zero graders.** `EvalResult.passed` returns `False`
   for an ungraded run — by design, so this fails loudly.
6. **Change the fixture and the answer key together.** They are one artifact.

---

## Troubleshooting

**`TypeError: make_case() got an unexpected keyword argument 'graders'`**
- An older `cases.py`. `make_case` now takes `graders`; explicit graders are kept and
  the standard outcome bundle is only auto-added when none were supplied.

**A case reports `FAIL` with no grader lines**
- It has no graders. `EvalResult.passed` is `False` when `scores` is empty — an
  ungraded case is not a passing case. Give it at least one grader.

**`Error: 'around' timestamp is required` from `timeline`**
- `timeline` takes `files`, `around`, and optional `window`/`limit`. It does **not**
  take `start_time`/`end_time`. Check the schema in `cli.py` before writing the input.

**A `ToolOutputGrader` fails on a string you can see in the log file**
- Output is clamped to `output_char_budget` (500 in the suite). Your string is past
  the clamp. Assert on something in the first 500 characters, or on the shape of the
  result rather than a specific late line.

**Every outcome case fails with "answered without opening a single log"**
- You are running under `--mock`, where an unscripted outcome case has nothing to
  replay. The runner skips these automatically; if you forced one with `--case`,
  either give it a script or run it with `--nvidia`.

**`skipping 3 skill case(s)`**
- Expected. Pass `--skills-root ./skills`.

**`STATUS = ERROR`, `AttributeError: module aiohttp has no attribute SocketTimeoutError`**
- Environment, not agent: an `openai`/`aiohttp` version mismatch, the same one
  `COMPLETE_SETUP_HARNESS.md` documents. `pip install -U openai aiohttp`. Known-good:
  `openai 3.3.1`, `aiohttp 3.14.3`. `ERROR` rather than `FAIL` is the suite correctly
  telling you the failure was not the agent's.

**`No cases matched the given filters`** (exit 2)
- Check `--list-cases`. `--scenario`, `--layer` and `--case` intersect; they do not union.

**Fixtures disappear before you can read them**
- Use `--keep-logs`; they land in `./eval_logs/<scenario>/`.

### Debugging recipes

```bash
# Is the tool broken, or the agent?
python -m evals.runner --layer tool --verbose

# What did the agent actually see?
python -m evals.runner --mock --case traj-cascading_failure_tool_usage --verbose

# Inspect a fixture by hand
python -c "from evals.scenarios import materialize; print(materialize('red_herring','/tmp/rh'))"

# Grade a hand-built result - no agent, no LLM, no network
python -c "
from evals.case import EvalCase, EvalResult
from evals.graders import KeywordGrader
r = EvalResult(case_id='x', layer='outcome', final_answer='the deploy raised DB_POOL_SIZE')
c = EvalCase(id='x', layer='outcome', prompt='', scenario='cascading_failure')
print(KeywordGrader(keywords=['DB_POOL_SIZE','deploy']).grade(r, c))
"
```

That last one is the payoff of `case.py` importing nothing from the harness.

---

## Relationship to the Rest of the Project

| Document | Covers | Question it answers |
|---|---|---|
| `COMPLETE_SETUP_HARNESS.md` | The agent loop, tools, LLM clients, brakes | *How does the agent work?* |
| `COMPLETE_SETUP_EVAL.md` (this) | Scenarios, cases, graders, runner, reports | *Does it work, and did that change help?* |
| `COMPLETE_SETUP_SKILL.md` | Playbooks, `SkillLibrary`, the two skill tools | *What does the agent know how to do?* |

The three are one system. The harness supplies capability, skills supply procedure,
and the suite is the only one of the three that can tell you whether either is
working. Improvements to the harness are validated here; new scenarios here are what
tell you which skill to write next.

---

*Every command, flag, type name, and grader in this document was verified against the
source at the time of writing. When they disagree, the source wins — and the
disagreement is a bug in this file.*
