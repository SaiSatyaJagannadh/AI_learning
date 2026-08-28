# Complete Skill System Setup & Architecture
**Complete System Documentation - Everything Connected**

## Table of Contents
1. [System Overview](#system-overview)
2. [Complete Architecture](#complete-architecture)
3. [File-by-File Breakdown](#file-by-file-breakdown)
4. [The Skill Format](#the-skill-format)
5. [The Shipped Playbooks](#the-shipped-playbooks)
6. [Data Flow](#data-flow)
7. [How Everything Connects](#how-everything-connects)
8. [Running It](#running-it)
9. [Writing a New Skill](#writing-a-new-skill)
10. [Production Implementation Guide](#production-implementation-guide)
11. [Troubleshooting](#troubleshooting)

---

## System Overview

The harness gives the agent **capabilities** — five tools that read log files. The eval
suite measures **outcomes**. Neither one carries **procedure**: the knowledge that a
502 storm is diagnosed by finding the *first* bad timestamp in the gateway and then
walking a timeline backwards through every other log, and that the cause will probably
be an INFO line nobody would grep for.

That knowledge has to live somewhere, and there are only three places to put it:

1. **In the system prompt.** Free at call time, but every skill you add taxes every
   request forever, including the ones that need none of them.
2. **In the model weights.** Not available to us.
3. **On disk, loaded on demand.** Costs one tool call when it is actually needed and
   nothing at all when it is not.

This is option 3 — and it is the same trade the log tools already make. The agent does
not read every log file up front; it calls `list_logs` and then opens the one that
matters. A skill is a markdown playbook the agent lists cheaply and reads only when the
description matches the problem in front of it.

**Key Innovation:** procedure is data, not code. A playbook is a markdown file. Anyone
who can debug an incident can write one, and the unit tests keep them honest without
anyone having to touch Python.

---

## Complete Architecture

### The Big Picture

```
┌─────────────────────────────────────────────────────────────────┐
│                    skills/  (on disk, editable by anyone)        │
│                                                                  │
│   cascading-failure/SKILL.md     resource-exhaustion/SKILL.md   │
│   expiry-and-config/SKILL.md     precision-check/SKILL.md       │
│                                                                  │
│   Each: YAML-ish frontmatter (the INDEX) + markdown (the BODY)  │
└────────────────────────────┬────────────────────────────────────┘
                             │  os.walk, looking for SKILL.md
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│              SkillLibrary  (logagent/skills.py)                  │
│  • reload()      - scan the tree, parse each file                │
│  • load_errors   - unreadable / duplicate skills, never raised   │
│  • validate()    - do the named tools actually exist?            │
│  • list_skills() - the cheap tool: one line per skill            │
│  • load_skill()  - the expensive tool: the full body, clamped    │
└────────────────────────────┬────────────────────────────────────┘
                             │  register_skill_tools(registry, library)
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                 ToolRegistry  (logagent/tools.py)                │
│   list_logs  read_log  search_logs  log_stats  timeline          │
│   + list_skills  + load_skill        <- two more, same interface │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                 AgentHarness  (logagent/harness.py)              │
│   The loop does not know skills exist. They are tools.           │
└─────────────────────────────────────────────────────────────────┘
```

The last box is the whole design. **A skill is not a new concept in the harness.** It
is two more entries in the tool registry, returning `{"content": [...], "is_error":
bool}` like everything else. `harness.py` was not modified to support this, and no
line of it mentions skills.

### Why two tools instead of one

The same reason the log tools are `list_logs` plus `read_log`, and not
`read_all_logs`: **listing is cheap and always safe to call; reading costs context and
should be a decision the model makes deliberately.**

With one combined tool, the agent pays for every playbook to find out which one it
wanted. With two, the index is ~4 lines and the body is only loaded when its
description matched the problem.

This puts real weight on the `description` field. It is the entire basis on which a
skill is selected. A vague description is therefore not a documentation problem — it
is a **retrieval bug**. The skill will simply never be picked.

---

## File-by-File Breakdown

### Core Files

#### `logagent/skills.py` — the whole implementation (~300 lines with comments)

```python
SKILL_FILE = "SKILL.md"                  # one well-known filename, no config format
DEFAULT_SKILL_CHAR_BUDGET = 4000         # same clamping discipline as the log tools

@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: str
    tools: List[str] = field(default_factory=list)

    def index_entry(self) -> str:        # what list_skills prints
        line = f"{self.name}: {self.description}"
        if self.tools:
            line += f" (uses: {', '.join(self.tools)})"
        return line
```

**The parser.** About twenty lines, hand-rolled, no YAML dependency:

```python
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)

def parse_skill(text: str, path: str = "") -> Skill:
    match = _FRONTMATTER_RE.match(text)
    meta, body = {}, text
    if match:
        raw_meta, body = match.group(1), match.group(2)
        for line in raw_meta.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, _, value = line.partition(":")
            meta[key.strip().lower()] = value.strip().strip("'\"")

    name        = meta.get("name") or _fallback_name(path)      # parent dir name
    description = meta.get("description") or _first_line(body)  # first non-empty line
    tools       = [t.strip() for t in meta.get("tools", "").split(",") if t.strip()]
    return Skill(name=name, description=description, body=body.strip(), path=path, tools=tools)
```

Adding PyYAML to read four scalar keys is the kind of thing that looks harmless and
then owns your install story. If the frontmatter ever needs nesting, *that* is the
moment to reach for a real parser — not before.

The parser is **lenient on purpose**: a file with no frontmatter is not an error. Its
name falls back to the directory name and its description to the first non-empty line.
A plain markdown note dropped into `skills/` still works, and that is what makes the
format worth using.

**Discovery.** A directory walk looking for one filename:

```python
def reload(self) -> None:
    self._skills, self.load_errors = {}, []
    if not os.path.isdir(self.skills_root):
        return                                    # missing root is empty, not fatal

    for dirpath, _dirnames, filenames in os.walk(self.skills_root):
        if SKILL_FILE not in filenames:
            continue
        path = os.path.join(dirpath, SKILL_FILE)
        try:
            with open(path, "r", encoding="utf-8") as f:
                skill = parse_skill(f.read(), path=path)
        except (OSError, UnicodeDecodeError) as e:
            self.load_errors.append(f"{path}: {e}")     # recorded, not raised
            continue
        if skill.name in self._skills:
            self.load_errors.append(f"{path}: duplicate skill name {skill.name!r} ...")
            continue
        self._skills[skill.name] = skill
```

**One broken playbook must not stop an agent that was going to use a different one.**
Errors accumulate in `load_errors`, which `cli.py` prints as warnings at startup.
Duplicates are reported rather than silently shadowed — a skill that quietly vanished
because someone copy-pasted a frontmatter block is a bug that takes an hour to find.

**Validation.** Called once at startup:

```python
def validate(self, registry_tool_names: List[str]) -> List[str]:
    known = set(registry_tool_names)
    problems = []
    for skill in self.list_skills_sorted():
        missing = [t for t in skill.tools if t not in known]
        if missing:
            problems.append(f"skill {skill.name!r} references unregistered tool(s): "
                            + ", ".join(missing))
    return problems
```

A playbook that says "call `grep_logs`" when the tool is named `search_logs` produces
an agent that confidently calls a tool that does not exist, and the failure surfaces
three turns later as a confused transcript. Catching it at startup costs nothing.

**The two tools.**

```python
def list_skills(self, args):
    skills = self.list_skills_sorted()
    if not skills:
        return {"content": [{"type": "text", "text":
            f"No skills found under {self.skills_root!r}. Proceed without a playbook."}],
            "is_error": False}
    lines  = [f"{len(skills)} skill(s) available:"]
    lines += [f"  {s.index_entry()}" for s in skills]
    lines.append("Call load_skill with a name to read the full playbook.")
    return {"content": [{"type": "text", "text": "\n".join(lines)}], "is_error": False}
```

An empty result *with advice* beats an empty result. The model must be able to tell
"no skills installed" from "the tool is broken", and the last line tells it what to do
next — the same principle the log tools follow.

```python
def load_skill(self, args):
    name = (args.get("name") or "").strip()
    if not name:
        return self._error("Error: 'name' is required. Call list_skills first ...")

    skill = self._skills.get(name)
    if skill is None:
        available = ", ".join(s.name for s in self.list_skills_sorted())
        return self._error(f"Error: no skill named {name!r}. Available: {available}.")

    header = f"# Skill: {skill.name}\n{skill.description}\n\n"
    budget = max(0, self.output_char_budget - len(header))
    text = skill.body
    if len(text) > budget:
        held = len(text) - budget
        text = text[:budget] + f"\n\n[{held} chars not shown - full playbook at {skill.path}]"
    return {"content": [{"type": "text", "text": header + text}], "is_error": False}
```

Two harness principles applied verbatim:

- **Error results, not exceptions.** An unknown skill returns `is_error: True` with a
  message that *names the alternatives*. The model recovers on the next turn instead of
  burning one guessing at names.
- **Clamp with honesty.** Never truncate silently. The notice says how many characters
  were held back and where the full text lives.

**Registration** — kept in `skills.py`, not `cli.py`:

```python
def register_skill_tools(registry, library: SkillLibrary) -> None:
    registry.register_tool(name="list_skills", description=..., parameters=LIST_SKILLS_SCHEMA,
                           function=library.list_skills, dangerous=False)
    registry.register_tool(name="load_skill",  description=..., parameters=LOAD_SKILL_SCHEMA,
                           function=library.load_skill,  dangerous=False)
```

One function, two callers: `cli.py` and `evals/runner.py::build_registry`. If the two
ever drift, the eval suite stops measuring the agent that actually ships.

The tool descriptions are part of the design, not decoration:

> `list_skills` — "*Call this first when starting an unfamiliar investigation — a
> matching playbook saves several turns of guessing.*"
>
> `load_skill` — "*Load a skill only when its description matches the problem; each
> one costs context.*"

The model's behaviour is a function of its tool descriptions as much as of its prompt.
These two say **when to call** and **when not to**, which is the part a name alone
cannot carry.

#### `cli.py` — the wiring (one flag, one block)

```python
parser.add_argument("--skills-root", type=str, default="./skills",
                    help="Directory of SKILL.md playbooks (default: ./skills; pass '' to disable)")

# Registered after the log tools so a playbook can be validated against the
# tools it names.
if args.skills_root:
    library = SkillLibrary(skills_root=args.skills_root)
    register_skill_tools(registry, library)
    for problem in library.load_errors + library.validate(
        [t["name"] for t in registry.list_tools()]
    ):
        print(f"warning: {problem}", file=sys.stderr)
```

Order matters: skills are registered **after** the five log tools so `validate()` sees
the complete tool list. Warnings go to `stderr` and do not stop the run — a malformed
playbook should degrade the agent, not ground it.

#### `skills/*/SKILL.md` — the playbooks

Four shipped. See [The Shipped Playbooks](#the-shipped-playbooks).

### Supporting Files

| File | Purpose |
|---|---|
| `tests/test_skills.py` | 10 tests, no network, no API key |
| `evals/cases.py` → `SKILL_CASES` | Three eval cases scoring the shipped playbooks |
| `evals/runner.py` → `build_registry(..., skills_root=)` | Registers the skill tools for eval runs |

`tests/test_skills.py` concentrates on the malformed cases, because a skill system is
only useful if a bad playbook degrades instead of exploding — and playbooks are the
part of the system non-programmers will edit:

| Test | Guards |
|---|---|
| `test_parses_frontmatter` | Frontmatter is stripped from the body, not left in it |
| `test_no_frontmatter_falls_back_to_dir_name_and_first_line` | A plain note still works |
| `test_missing_root_is_empty_not_an_error` | No `skills/` directory is not a crash |
| `test_discovery_and_load` | The happy path |
| `test_unknown_skill_is_an_error_result_naming_the_alternatives` | Recovery in one turn |
| `test_body_is_clamped_with_an_explicit_notice` | Never truncate silently |
| `test_validate_flags_tools_that_are_not_registered` | Catch the drift at startup |
| `test_duplicate_names_are_reported_not_silently_shadowed` | No vanishing skills |
| `test_register_skill_tools_wires_both_tools` | Exactly two tools, both callable |
| `test_shipped_skills_are_valid` | **The four real playbooks parse and name real tools** |

That last one is the one that matters day to day: it means a typo in a markdown file
turns the test suite red.

---

## The Skill Format

```markdown
---
name: cascading-failure
description: A downstream error storm (502s, timeouts) whose real cause is an upstream change minutes earlier in a different log.
tools: list_logs, search_logs, timeline
---

## When to use this
...

## Procedure
1. ...

## What to look for
...

## How to state the answer
...
```

### Frontmatter

| Key | Required | Purpose |
|---|---|---|
| `name` | No — falls back to the directory name | The id passed to `load_skill` |
| `description` | No — falls back to the first non-empty body line | **The retrieval key.** The only thing the model sees when choosing |
| `tools` | No | Comma-separated. Advisory to the model, checked by `validate()` at startup |

### Body

Free markdown. The four-section shape the shipped playbooks use is a convention, not a
constraint, and each section earns its place:

| Section | Answers |
|---|---|
| **When to use this** | The symptom shape, so the model can reject the skill quickly |
| **Procedure** | Numbered steps naming actual tools and actual arguments |
| **What to look for** | The specific trap — usually where the naive search fails |
| **How to state the answer** | What a complete diagnosis contains, so the answer is not a restatement of the question |

### Rules that make a playbook work

1. **The description names the symptom, not the cause.** The agent is holding a symptom
   when it chooses; it does not yet know the cause. "A downstream error storm (502s,
   timeouts)" is selectable. "DB_POOL_SIZE misconfiguration" is not.
2. **Steps name real tools with real arguments.** "Correlate the logs" is not a step.
   "`timeline` with `around` set to that first timestamp and **every** log file in
   `files`" is.
3. **Say what the naive approach misses.** The value of the cascading-failure playbook
   is one sentence: *config changes are logged at INFO, so severity filtering finds the
   symptom and hides the cause.* That is the part the model does not already know.
4. **Keep it under the clamp.** 4000 characters by default. A playbook longer than that
   gets cut, and the part you cut is the part you wrote last.

---

## The Shipped Playbooks

| Skill | Triggers on | Core insight |
|---|---|---|
| `cascading-failure` | 502s, upstream timeouts, "could not acquire connection" | The log that is screaming is not the log that broke. Find the **first** bad timestamp, then `timeline` every file around it. Config changes are logged at INFO |
| `resource-exhaustion` | `100%`, `50/50`, `32 of 32`, `ENOSPC`, `OOM` | Search for the **limit** language, not the failure language. Find where the metric *crossed*, not where it plateaued. Growth shape names the cause: steady climb = leak, step change = deploy |
| `expiry-and-config` | Failures starting at a wall-clock instant with no load change | Time is the trigger. Search the stem `expir` to catch expires/expired/expiry in one pass. Find the **warning before the failure** — expiries are nearly always announced |
| `precision-check` | Always, before committing to an answer | Three tests: **precedence** (strictly before, not same-second), **mechanism** (state the chain without "somehow"), **coverage** (explains every error, not just the colourful one) |

`precision-check` is the odd one out and the most valuable. The other three help the
agent *find* something; this one stops it from being satisfied too early. It maps
directly onto the `red_herring` scenario in the eval suite and onto
`ForbiddenKeywordGrader`, which is the only grader that measures precision rather than
recall:

> Finding *an* anomaly and calling it *the* cause. Real incidents contain several
> anomalies at once; most are fellow victims. A cache miss rate spiking from 3% to 47%
> at the same instant as the errors is exactly as consistent with "the cache broke
> everything" as with "everything broke, so the cache is being missed" — and the second
> is far more common.

Note what the four playbooks are **not**: one per scenario. `cert_expiry` and a rotated
API key share `expiry-and-config`; `disk_full` and `memory_leak` share
`resource-exhaustion`. One skill per scenario would be a lookup table that only works
on incidents you have already had.

---

## Data Flow

### An investigation that uses a skill

```
python cli.py --initial-prompt "Why are we seeing 502s?" --nvidia
  │
  ├─ STARTUP
  │    registry <- 5 log tools
  │    SkillLibrary("./skills").reload()
  │      └─► os.walk finds 4 SKILL.md files, parses each
  │    register_skill_tools(registry, library)      # now 7 tools
  │    validate(["list_logs",...,"load_skill"]) -> []   # nothing to warn about
  │
  ├─ Turn 1   LLM -> tool_use  list_skills{}
  │           4 skill(s) available:
  │             cascading-failure: A downstream error storm (502s, timeouts)...
  │             expiry-and-config: Failures that start at a wall-clock instant...
  │             precision-check: Run before answering...
  │             resource-exhaustion: Something finite ran out...
  │           Call load_skill with a name to read the full playbook.
  │                                                        (~4 lines of context)
  │
  ├─ Turn 2   LLM -> tool_use  load_skill{name: "cascading-failure"}
  │           # Skill: cascading-failure
  │           ... the full procedure ...            (~1.5 KB, clamped at 4000)
  │
  ├─ Turn 3   LLM -> tool_use  search_logs{pattern: "502", file: "gateway.log"}
  │             ^ step 2 of the playbook: find the FIRST occurrence
  │
  ├─ Turn 4   LLM -> tool_use  timeline{files: [all four], around: "<first 502>"}
  │             ^ step 3: the cause and the effect are adjacent in time and far
  │               apart in filename
  │
  └─ Turn 5   LLM -> stop
              "The 2.3.0 deploy at 10:03:00 raised DB_POOL_SIZE from 10 to 100
               across 12 instances against a database with max_connections=50..."
              ^ the shape the playbook's "How to state the answer" asked for
```

Two turns spent on skills; three doing the work. Without the playbook those three turns
are typically five or six, several of them spent grepping `ERROR` in the file that was
already known to be full of them.

### The clamp, concretely

```
skill body            1,487 chars
header                   ~140 chars   ("# Skill: <name>\n<description>\n\n")
budget                 4,000 chars
                      ────────────
delivered              1,627 chars, no clamp notice

# and if the body were 6,000 chars:
delivered              4,000 chars + "[2,140 chars not shown - full playbook at
                                      skills/cascading-failure/SKILL.md]"
```

The path in the notice is deliberate. It is actionable for a human reading the
transcript, which is who ends up fixing an over-long playbook.

---

## How Everything Connects

### The Connection Map

```
skills/*/SKILL.md
   │  os.walk + parse_skill()
   ▼
SkillLibrary ────────────┬─► list_skills()  ──┐
   │                     └─► load_skill()   ──┤
   │ validate()                               │  function=
   │    ▲                                     ▼
   │    │                          register_skill_tools()
   │    │                                     │
   │    │                                     ▼
   │    └──────── tool names ────────── ToolRegistry ◄── the 5 log tools
   │                                          │
   │                                          ▼
   │                                    AgentHarness.run()
   │                                          │
   │                                          ▼
   └── load_errors ──► cli.py warnings   the loop - unmodified,
                                         skills are just tools

Two callers of register_skill_tools():
   cli.py                                (--skills-root, default ./skills)
   evals/runner.py::build_registry()     (--skills-root, default off)
```

### The Dependency Graph

```
                    ┌──────────────────────┐
                    │  logagent/skills.py  │
                    └──────────┬───────────┘
                               │
              ┌────────────────┴────────────────┐
              ▼                                 ▼
      ┌───────────────┐                 ┌────────────────┐
      │  skills/ tree │                 │  ToolRegistry  │
      │  (SKILL.md)   │                 │ (logagent/     │
      └───────────────┘                 │   tools.py)    │
                                        └───────┬────────┘
                                                ▼
                                        ┌────────────────┐
                                        │  AgentHarness  │
                                        └────────────────┘

logagent/skills.py depends on:
  • os, re, dataclasses, typing        (stdlib only - no YAML, no third party)
  • a directory of SKILL.md files      (optional; a missing one is empty, not fatal)
  • ToolRegistry                       (duck-typed - register_skill_tools takes any
                                        object with .register_tool)

cli.py depends on:            SkillLibrary, register_skill_tools
evals/runner.py depends on:   SkillLibrary, register_skill_tools
logagent/harness.py depends on: NOTHING from skills.py
```

That last line is the architectural claim of the whole file. The agent loop was not
modified to support skills, and it cannot tell a skill tool from a log tool. Anything
that can be expressed as `{"content": [...], "is_error": bool}` plugs in the same way.

---

## Running It

Skills are on by default — `--skills-root` defaults to `./skills`.

```bash
# Normal run: the agent can list and load playbooks
python cli.py --initial-prompt "Why are we seeing 502s?" --nvidia

# Turn skills off (A/B the value of the playbooks)
python cli.py --initial-prompt "Why are we seeing 502s?" --nvidia --skills-root ''

# Point at a different playbook set
python cli.py --initial-prompt "..." --nvidia --skills-root ./team-playbooks
```

Exercise the library directly, no agent required:

```bash
python -c "
from logagent.skills import SkillLibrary
lib = SkillLibrary('./skills')
print(lib.list_skills({})['content'][0]['text'])
print('errors:', lib.load_errors)
print('unknown tools:', lib.validate(['list_logs','read_log','search_logs','log_stats','timeline']))
"
```

Tests and evals:

```bash
python -m pytest tests/test_skills.py -v            # 10 tests, no key, no network
python -m evals.runner --mock --skills-root ./skills
```

Without `--skills-root` the eval runner skips the skill cases with a message rather
than failing them on "unknown tool" — a wiring problem dressed up as a test failure
helps nobody:

```
skipping 3 skill case(s): pass --skills-root ./skills to run them
```

### The skill eval cases

| Case | Asserts |
|---|---|
| `tool-list_skills` | The index names every playbook and tells the model about `load_skill` |
| `tool-load_skill` | Loading returns the **procedure**, not just the title |
| `tool-load_skill-unknown` | `expect_error=True` **and** the error names the alternatives |

The third is a negative control, and it is the one worth having. A tool that fails
helpfully is what keeps the agent from burning a turn guessing at names — and nothing
else in the suite would notice if that message regressed.

---

## Writing a New Skill

### 1. Create the file

```bash
mkdir -p skills/slow-queries
cat > skills/slow-queries/SKILL.md <<'MD'
---
name: slow-queries
description: Latency climbs across every endpoint at once with no error spike and no deploy.
tools: search_logs, log_stats, timeline
---

## When to use this
Everything still works, just slowly. p99 climbs, error rate does not. This rules
out a broken dependency and points at contention or a plan change.

## Procedure
1. `log_stats` first - confirm the error counts really are flat. If they are not,
   this is the wrong playbook.
2. `search_logs` for duration language: `ms`, `slow query`, `lock wait`, `seq scan`.
3. `timeline` around the point latency started climbing, with the database log
   included. Contention shows as waits that predate the slow responses.

## What to look for
The *shape* of the climb. A step means a plan flipped or a config changed. A ramp
means something is growing - a table, a queue, a cache that stopped being warm.

## How to state the answer
Name what is contended and what is waiting on it. "Queries got slower" is the
observation you started with, not the diagnosis.
MD
```

### 2. Verify it

```bash
python -c "
from logagent.skills import SkillLibrary
lib = SkillLibrary('./skills')
print(lib.load_errors)
print(lib.validate(['list_logs','read_log','search_logs','log_stats','timeline']))
print(lib.load_skill({'name':'slow-queries'})['content'][0]['text'][:200])
"
python -m pytest tests/test_skills.py -q       # test_shipped_skills_are_valid covers it
```

### 3. Add an eval case (optional, recommended)

In `evals/cases.py`, append to `SKILL_CASES`:

```python
make_case(
    case_id="tool-load_skill-slow-queries",
    layer="tool",
    scenario_name="cascading_failure",
    prompt="",
    rationale="The slow-queries playbook must name log_stats as the first step",
    tool_name="load_skill",
    tool_input={"name": "slow-queries"},
    max_turns=1, max_tool_calls=1,
    graders=[ToolOutputGrader(must_contain=["log_stats", "contention"], min_lines=5)],
)
```

### The checklist

- [ ] `description` names the **symptom**, so the agent can match it before it knows the cause
- [ ] Every tool in `tools:` is really registered (`validate()` will tell you)
- [ ] Procedure steps name tools and arguments, not intentions
- [ ] There is at least one sentence the model would not have guessed
- [ ] Body is under 4000 characters
- [ ] It generalises past one incident — if it only ever fires once, it is a runbook, not a skill

---

## Production Implementation Guide

### When a skill is the right answer

| Situation | Put it in |
|---|---|
| Applies to **every** request ("always cite the file and line") | The system prompt |
| Applies to **one class** of problem, and there are several classes | A skill |
| Is a **capability**, not a procedure ("read a log file") | A tool |
| Is one specific incident's write-up | A runbook in your wiki — not here |

The dividing line is frequency. Anything in the system prompt is paid for on every
request forever. A skill is paid for only by the investigations that load it.

### Scaling the library

- **10 skills:** the index is ~10 lines. Nothing to do.
- **50 skills:** the index starts to cost real context. Group the tree by domain
  (`skills/db/`, `skills/network/`) — `os.walk` already recurses — and add a `domain`
  frontmatter key plus a filter argument to `list_skills`.
- **200 skills:** listing stops being the right retrieval mechanism. Embed the
  descriptions and make `list_skills` a similarity search. The tool interface does not
  change, which is the point of having put the index behind a tool call in the first
  place.

Do not pre-build for 200. The `SkillLibrary` seam is where that upgrade lands, and
until the index actually hurts, a sorted list is faster, debuggable, and free.

### Operational notes

- **Reloading.** `reload()` is public and re-scans the tree. Nothing calls it after
  startup today. A long-running service that lets people edit playbooks live should
  call it on a file watcher — deliberately not wired up, because a process that
  re-reads its instructions mid-investigation is hard to reason about in a transcript.
- **Trust boundary.** Playbook text goes into the model's context. `skills/` is
  application code, not user input — treat write access to it as write access to the
  system prompt. Never point `--skills-root` at a directory users can write to.
- **Path safety.** Unlike `logtools.py`, there is no path confinement here, because
  nothing takes a path from the model: `load_skill` takes a *name* and looks it up in a
  dict built at startup. Names never touch the filesystem. Keep it that way — a
  `path` argument on `load_skill` would be an arbitrary-file-read tool.
- **Versioning.** Playbooks are markdown in git. `git log skills/` is the change
  history, and a diff on a playbook is a diff on agent behaviour — review it like code.

### Best Practices

1. **One skill per problem class, not per incident.** If it fires once, it is a runbook.
2. **Write the description last,** after the body, when you know what the playbook is
   actually for. It is the retrieval key and it deserves more care than the title.
3. **Keep `validate()` clean.** A warning at startup is a playbook that will misfire.
4. **A/B it.** `--skills-root ''` disables skills. Run the outcome layer both ways; if
   the playbook does not move the score, it is context you are paying for and not using.
5. **Prune.** A skill that is never loaded still costs index lines on every
   investigation. Delete it.

---

## Troubleshooting

**The agent never calls `list_skills`**
- Nothing in the prompt suggests it should. Either mention playbooks in the initial
  prompt, or accept it: the tool description says "call this first when starting an
  unfamiliar investigation", and a model that already knows the answer is right to skip it.

**The agent lists skills but loads the wrong one**
- A `description` problem, not a model problem. Descriptions must be distinguishable
  from each other **using only the symptom**. If two descriptions could plausibly match
  the same first observation, merge them or sharpen both.

**`warning: skill 'x' references unregistered tool(s): y`**
- The `tools:` frontmatter names a tool that is not in the registry. Fix the spelling,
  or register the tool. This is `validate()` doing its job at startup instead of
  letting the agent discover it three turns in.

**`warning: ... duplicate skill name 'demo', keeping <path>`**
- Two `SKILL.md` files declare the same `name:`. Usually a copy-pasted frontmatter
  block. The first one found wins; rename the other.

**`No skills found under './skills'`**
- Wrong working directory, or `--skills-root` points somewhere else. The library
  requires `<dir>/<anything>/SKILL.md` — a bare `skills/foo.md` is not discovered, by
  design: one well-known filename means discovery is a directory walk, not a config format.

**A loaded skill is cut off mid-sentence**
- It exceeded `output_char_budget` (4000). The notice names the file and the number of
  characters held back. Split it into two skills, or cut the prose — a playbook longer
  than 4000 characters usually contains a second playbook.

**Skill eval cases fail with "unknown tool"**
- The runner was not given `--skills-root ./skills`. Without it the two skill tools are
  never registered.

**A skill parses but `description` reads `(no description)`**
- No frontmatter *and* an empty body. Add a `description:` key, or at minimum one
  non-empty line — the first non-empty line becomes the description.

---

## Relationship to the Rest of the Project

| Document | Covers | Question it answers |
|---|---|---|
| `COMPLETE_SETUP_HARNESS.md` | The agent loop, tools, LLM clients, brakes | *How does the agent work?* |
| `COMPLETE_SETUP_EVAL.md` | Scenarios, cases, graders, runner, reports | *Does it work, and did that change help?* |
| `COMPLETE_SETUP_SKILL.md` (this) | Playbooks, `SkillLibrary`, the two skill tools | *What does the agent know how to do?* |

The three layers answer three different questions, and they share one interface — the
tool result dict — which is why adding skills required no change to `harness.py` at all:

```
capability  (harness)  ─┐
procedure   (skills)   ─┼─►  {"content": [...], "is_error": bool}
measurement (evals)    ─┘         the only contract in the system
```

Write a skill when the eval suite shows the agent *can* find the answer but takes six
turns to do it. Write a tool when it *cannot* find the answer at all. The eval suite is
how you tell those two apart.

---

*Every command, flag, filename, and code excerpt in this document was verified against
the source at the time of writing. When they disagree, the source wins — and the
disagreement is a bug in this file.*
