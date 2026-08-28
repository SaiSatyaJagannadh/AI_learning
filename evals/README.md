# Evaluation Suite

This directory contains the evaluation framework for testing the log agent harness. The eval suite measures the agent's ability to investigate and diagnose issues in log files using the harness architecture.

## Overview

The evaluation suite consists of:

1. **Scenarios** (`scenarios.py`): Different log file configurations representing various failure modes
2. **Cases** (`cases.py`): Specific questions/prompts to ask the agent, organized by evaluation layer
3. **Graders** (`graders.py`): Functions that score agent responses
4. **Runner** (`runner.py`): Executes cases against the agent and aggregates results
5. **Supporting modules**:
   - `case.py`: Core data types (EvalCase, GroundTruth, etc.)
   - `replay.py`: Scripted LLM responses for trajectory layer
   - `report.py`: Generates evaluation reports

## Scenarios

Five deterministic scenarios are provided, each generating its own set of log files:

1. **cascading_failure**: Deploy changes DB_POOL_SIZE overwhelming database connections
2. **disk_full**: Storage device runs out of space causing write failures
3. **memory_leak**: Unbounded memory growth leading to OOM kills and restart loops
4. **cert_expiry**: TLS certificate expiration causing handshake failures
5. **red_herring**: Thread pool exhaustion with misleading cache metrics (tests precision)

Each scenario includes:
- A log file generator that creates deterministic log files
- Ground truth with root cause keywords, forbidden keywords, and expected tools
- A description of the failure and expected diagnosis

## Evaluation Layers

Cases are organized into three layers to test different aspects of the agent:

### Tool Layer
- Direct invocation of individual tools (list_logs, read_log, search_logs, timeline)
- No LLM involved - tests tool correctness in isolation
- Useful for verifying tool implementations

### Trajectory Layer
- Scripted LLM interactions that prescribe which tools to call
- Tests whether the agent follows the expected tool usage pattern
- Uses mock LLM that returns predefined tool_use blocks

### Outcome Layer
- Live LLM interactions using the actual harness
- Tests end-to-end ability to diagnose issues from log files
- Measures final answer quality and tool usage

## Case Types

Each scenario has cases across the layers:

1. **Tool Cases**: Test individual tools
   - Example: `tool-search_logs` - tests search_logs tool with specific pattern

2. **Trajectory Cases**: Check tool usage patterns
   - Example: `traj-cascading_failure_tool_usage` - verifies agent uses appropriate tools

3. **Outcome Cases**: Test final diagnosis
   - Example: `outcome-cascading_failure` - asks agent to find root cause of 502 errors

## Running the Evaluation

### Prerequisites
1. Install dependencies: `pip install -r requirements.txt`
2. For NVIDIA API evaluation: Copy `.env.example` to `.env` and add your NVIDIA API key

### Run All Evaluations
```bash
# Using mock LLM (no API key needed)
python -m evals.runner --mock

# Using NVIDIA API
python -m evals.runner --nvidia

# Using specific scenario
python -m evals.runner --scenario cascading_failure --nvidia

# Using specific case
python -m evals.runner --case outcome-cascading_failure --nvidia
```

### Available Options
```
--mock           Use MockClient for testing (default if neither --mock nor --nvidia)
--nvidia         Use NVIDIA API with models like meta/llama-3.3-70b-instruct
--model MODEL    Override the model (e.g., meta/llama-3.1-70b-instruct)
--scenario NAME  Run only cases from the specified scenario
--case ID        Run only the specified case ID
--verbose        Show detailed output including tool calls and results
--list-cases     List all available case IDs
--list-scenarios List all available scenarios
--layer LAYER    Run only tool | trajectory | outcome cases
--skills-root DIR  Register list_skills/load_skill (required by the skill cases)
--api-key KEY    NVIDIA key; defaults to $NVIDIA_API_KEY from .env
--out DIR        Also write results.json and report.md
--keep-logs      Generate fixtures into ./eval_logs instead of a temp dir
```

Exit code is 0 if every case passed, 1 if any failed, 2 if the filters matched
nothing. Under `--mock` the live-model outcome cases and (without `--skills-root`)
the skill cases are skipped with a message rather than failing by construction.

Full reference: `COMPLETE_SETUP_EVAL.md`.

## Understanding Results

After running evaluations, you'll see:

1. **Per-case results**: Whether each case passed or failed
2. **Grader breakdown**: Which specific graders passed/failed (keywords, tool usage, etc.)
3. **Aggregated scores**: Overall performance across all cases
4. **Tool call trace**: Exact sequence of tools used (with --verbose)
5. **Duration and turn counts**: Performance metrics

### Grader Types
- **KeywordGrader**: Checks that root cause keywords appear in final answer
- **ForbiddenKeywordGrader**: Ensures misleading keywords (decoys) are absent
- **ToolTrajectoryGrader**: Verifies that expected tools were actually used
- **BrakeGrader**: Confirms the agent didn't hit execution limits prematurely
- **ToolOutputGrader**: For tool-layer cases, validates tool output correctness
- **LLMJudgeGrader**: Optional LLM-based evaluation of answer quality

## Adding New Evaluations

### To Add a New Scenario
1. Add a generator function in `scenarios.py` (following the _gen_* pattern)
2. Add a Scenario instance to the SCENARIOS dict
3. The ground truth should specify:
   - `root_cause_keywords`: Words that must appear in correct diagnosis
   - `forbidden_keywords`: Words that indicate wrong diagnosis (if applicable)
   - `expected_tools`: Minimum tools needed to properly investigate
   - `culprit_file` and `culprit_timestamp`: Where/when the root cause is logged
   - `description`: Human-readable explanation

### To Add New Cases
1. Edit `cases.py` to add new EvalCase instances
2. Choose appropriate layer:
   - Tool layer for direct tool testing
   - Trajectory layer for tool usage patterns
   - Outcome layer for end-to-end diagnosis
3. Reference existing scenarios by name
4. Add appropriate graders if needed beyond the defaults

### To Add New Graders
1. Implement a new Grader subclass in `graders.py`
2. Add it to case.graders lists as needed

## Design Principles

1. **Determinism**: All log files are generated deterministically from a fixed timestamp
2. **Isolation**: Each scenario gets its own temporary directory
3. **Actionable Feedback**: Graders provide specific failure reasons
4. **Progressive Disclosure**: Layers test increasingly complex capabilities
5. **Composability**: Mix and match scenarios, cases, and graders

## Example: How an Outcome Case Works

For `outcome-cascading_failure`:
1. Runner creates a temporary directory
2. cascading_failure scenario generates deploy.log, service.log, gateway.log, database.log
3. Agent receives prompt: "What is the root cause of the 502 errors..."
4. Harness drives the agent loop:
   - Gets LLM completion
   - Executes requested tools (via instrumented registry)
   - Feeds tool results back to LLM
   - Continues until stop condition or max turns
5. Runner collects:
   - Final answer (for keyword grading)
   - Tool call sequence (for trajectory grading)
   - Turn count and duration (for brake grading)
6. Graders evaluate:
   - Did answer mention "DB_POOL_SIZE" and "deploy"?
   - Did agent use search_logs and timeline tools?
   - Did agent avoid hitting max_turns/max_tool_calls unnecessarily?

This approach tests both the agent's reasoning ability and its effective use of the harness tools.
