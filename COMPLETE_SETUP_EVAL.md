# Complete Evaluation Suite Setup & Architecture
**Complete System Documentation - Everything Connected**

## Table of Contents
1. [System Overview](#system-overview)
2. [Complete Architecture](#complete-architecture)
3. [File-by-File Breakdown](#file-by-file-breakdown)
4. [Evaluation Layers](#evaluation-layers)
5. [How Everything Connects](#how-everything-connects)
6. [Running Evaluations](#running-evaluations)
7. [Adding New Evaluations](#adding-new-evaluations)
8. [Troubleshooting](#troubleshooting)

---

## System Overview

This is a **comprehensive evaluation suite** for testing the log agent harness. The evaluation suite:
1. Provides deterministic log scenarios representing various failure modes
2. Defines test cases across multiple layers (tool, trajectory, outcome)
3. Includes graders that automatically score agent performance
4. Executes evaluations and generates detailed reports
5. Measures the agent's ability to investigate issues, use tools correctly, and arrive at accurate diagnoses

## Complete Architecture

```
Evaluation Suite Components:
├── case.py          # Core data types (EvalCase, GroundTruth, etc.)
├── graders.py       # Scoring functions (KeywordGrader, ToolTrajectoryGrader, etc.)
├── scenarios.py     # Deterministic log generators and ground truth
├── cases.py         # Specific test cases organized by layer
├── runner.py        # Executes cases against the agent and aggregates results
├── replay.py        # Scripted LLM responses for trajectory layer testing
└── report.py        # Generates detailed evaluation reports
```

The evaluation suite works with the log agent harness by:
1. Creating temporary directories with scenario-specific log files
2. Instrumenting the tool registry to capture tool calls
3. Driving the agent loop with LLM clients (mock or real)
4. Collecting results (final answer, tool calls, turns, duration)
5. Applying graders to score performance
6. Aggregating results into a comprehensive report

## File-by-File Breakdown

### case.py
Defines the core data types used throughout the evaluation suite:
- **EvalCase**: A question/prompt to ask the agent, plus metadata and graders
- **GroundTruth**: The answer key for a scenario (root cause keywords, forbidden keywords, expected tools)
- **EvalResult**: Everything observable about a single agent run
- **GraderScore**: One grader's verdict on one run
- **SuiteReport**: Aggregate over many results
- **Layers**: Tool, trajectory, and outcome evaluation layers

### graders.py
Contains all scoring functions that implement different evaluation criteria:
- **KeywordGrader**: Checks that required keywords appear in the final answer
- **ForbiddenKeywordGrader**: Ensures misleading keywords (decoys) are absent
- **ToolTrajectoryGrader**: Verifies that expected tools were actually used
- **ToolOrderGrader**: Checks that tools were called in the expected sequence
- **NoToolErrorGrader**: Ensures no tool errors occurred during execution
- **BrakeGrader**: Confirms the agent didn't hit execution limits prematurely
- **ToolOutputGrader**: For tool-layer cases, validates tool output correctness
- **RegexGrader**: Final answer must match (or must not match) a regex pattern
- **LLMJudgeGrader**: Optional LLM-based evaluation of answer quality

### scenarios.py
Defines deterministic log file generators and ground truth for various failure modes:
- **cascading_failure**: Deploy changes DB_POOL_SIZE overwhelming database connections
- **disk_full**: Storage device runs out of space causing write failures
- **memory_leak**: Unbounded memory growth leading to OOM kills and restart loops
- **cert_expiry**: TLS certificate expiration causing handshake failures
- **red_herring**: Thread pool exhaustion with misleading cache metrics (tests precision)

Each scenario includes:
- A `generate` function that creates deterministic log files in a target directory
- **GroundTruth** with:
  - `root_cause_keywords`: Words that must appear in correct diagnosis
  - `forbidden_keywords`: Words that indicate wrong diagnosis (if applicable)
  - `expected_tools`: Minimum tools needed to properly investigate
  - `culprit_file` and `culprit_timestamp`: Where/when the root cause is logged
  - `description`: Human-readable explanation

### cases.py
Defines specific test cases organized by evaluation layer:
- **Tool Layer**: Direct invocation of individual tools (list_logs, read_log, search_logs, timeline)
- **Trajectory Layer**: Scripted LLM interactions that prescribe which tools to call
- **Outcome Layer**: Live LLM interactions using the actual harness to test end-to-end diagnosis

Each case includes:
- Unique ID and layer specification
- Prompt to ask the agent (empty for tool layer)
- Rationale explaining what the case tests
- Tool name and input (for tool layer cases)
- Optional script (for trajectory layer cases)
- Configuration (max turns, tool calls, output tokens)
- Graders that will be automatically added based on the scenario's ground truth

### runner.py
The execution engine that:
1. Discovers and loads test cases from cases.py
2. For each case:
   - Creates a temporary directory with scenario-specific log files
   - Builds an instrumented tool registry (wraps registry.execute_tool to capture calls)
   - Instantiates the appropriate LLM client (MockClient or NvidiaClient/ClaudeClient)
   - For tool layer: Directly invokes the specified tool
   - For trajectory/outcome layers: Drives the agent harness with the case prompt
   - Collects the EvalResult (final answer, tool calls, turns, duration, etc.)
   - Applies all graders from the case to produce GraderScores
3. Aggregates results into a SuiteReport
4. Handles errors gracefully (failed runs still get graded)

### replay.py
Provides scripted LLM responses for trajectory layer testing:
- Defines scripted sequences of tool_use blocks that the mock LLM will return
- Allows precise control over what tools the agent "decides" to use
- Essential for testing whether the agent follows expected tool usage patterns

### report.py
Generates detailed evaluation reports:
- Formats SuiteResult into human-readable output
- Shows per-case results with grader breakdowns
- Provides aggregated statistics and performance metrics
- Can output in various formats (text, JSON, etc.)

## Evaluation Layers

### Tool Layer
- **Purpose**: Test individual tool correctness in isolation
- **LLM Involvement**: None
- **How it works**: 
  - Directly invokes a single tool with specified inputs
  - Validates that the tool produces expected outputs
  - Uses ToolOutputGrader to check correctness
- **Use cases**: Verifying tool implementations, debugging specific tool issues

### Trajectory Layer
- **Purpose**: Test whether the agent follows expected tool usage patterns
- **LLM Involvement**: Scripted (mock LLM returns predefined tool_use blocks)
- **How it works**:
  - Mock LLM returns a predetermined sequence of tool_use blocks
  - Agent executes those tools and receives results
  - Evaluates whether agent used the expected tools (regardless of final answer)
  - Uses ToolTrajectoryGrader to verify tool usage
- **Use cases**: Testing agent's decision-making process, verifying tool selection logic

### Outcome Layer
- **Purpose**: Test end-to-end ability to diagnose issues from logs
- **LLM Involvement**: Live (actual LLM client - mock for testing, Nvidia/Claude for production)
- **How it works**:
  - Agent receives a natural language prompt about an issue
  - Harness drives the agent loop: LLM → tool execution → result feedback → repeat
  - Agent must investigate logs, identify root cause, and provide diagnosis
  - Evaluates final answer quality and tool usage patterns
  - Uses multiple graders: KeywordGrader, ForbiddenKeywordGrader, ToolTrajectoryGrader, BrakeGrader
- **Use cases**: End-to-end validation of agent capabilities, production readiness testing

## How Everything Connects

1. **Test Definition** (`cases.py`):
   - Developers define what to test (cases) and how to score it (graders)
   - Cases reference scenarios by name
   - Layers determine how the case is executed

2. **Scenario Setup** (`scenarios.py` + `runner.py`):
   - Runner creates temporary directory
   - Scenario's generate function creates deterministic log files
   - Ground truth provides scoring criteria

3. **Execution** (`runner.py` + `harness.py`):
   - For tool layer: Direct tool invocation
   - For trajectory/outcome layers: 
     - Runner builds instrumented tool registry
     - Runner instantiates LLM client
     - Runner creates AgentHarness with the client and registry
     - Harness drives the agent loop (LLM completion → tool execution → result feedback)
     - Tool calls are captured by the instrumented registry

4. **Scoring** (`graders.py` + `runner.py`):
   - Runner collects EvalResult from harness
   - Runner applies each grader from the case to the result
   - Graders return GraderScore (passed, score, detail)
   - Runner aggregates scores into SuiteReport

5. **Reporting** (`report.py`):
   - Formats SuiteResult into readable output
   - Shows which graders passed/failed per case
   - Provides overall statistics

## Running Evaluations

### Prerequisites
1. Install dependencies: `pip install -r requirements.txt`
2. For NVIDIA API evaluation: Copy `.env.example` to `.env` and add your NVIDIA API key

### Basic Commands
```bash
# Run all evaluations with mock LLM (no API key needed)
python -m evals.runner --mock

# Run all evaluations with NVIDIA API
python -m evals.runner --nvidia

# Run specific scenario
python -m evals.runner --scenario cascading_failure --nvidia

# Run specific case
python -m evals.runner --case outcome-cascading_failure --nvidia

# List all available cases
python -m evals.runner --list-cases

# List all available scenarios
python -m evals.runner --list-scenarios

# Run with verbose output (shows tool calls and details)
python -m evals.runner --nvidia --verbose

# Run with specific model
python -m evals.runner --nvidia --model meta/llama-3.1-70b-instruct
```

### Understanding Results
After running evaluations, you'll see:

```
=== EvalRun: mock ===
Passed: 8/10
Failed: 2/10

=== Case: outcome-cascading_failure ===
  Passed: True
  Grader Scores:
    KeywordGrader: 1.0/1.0 (required keywords: DB_POOL_SIZE, deploy)
    ForbiddenKeywordGrader: 1.0/1.0 (no forbidden keywords)
    ToolTrajectoryGrader: 1.0/1.0 (called: search_logs, timeline)
    BrakeGrader: 1.0/1.0 (completed within limits)
  Tool Calls:
    1. search_logs({"pattern": "DB_POOL_SIZE", "file": "deploy.log"})
    2. timeline({"files": [...], "start_time": "...", "end_time": "..."})
  Turns: 3
  Duration: 1.23s
```

### Key Metrics in Reports
- **Passed/Failed**: Whether the case met all grader thresholds
- **Grader Scores**: Individual performance on each criterion (0.0-1.0)
- **Tool Calls**: Exact sequence of tools used with their inputs
- **Turns**: Number of LLM interaction cycles completed
- **Duration**: Total execution time
- **Final Answer**: Agent's response (for outcome layer cases)

## Adding New Evaluations

### To Add a New Scenario
1. Add a generator function in `scenarios.py` (following the `_gen_*` pattern):
   ```python
   def _gen_new_scenario(directory: str) -> None:
       # Generate log files deterministically
       pass
   
   _NEW_SCENARIO_S = _at(datetime(2026, 8, 24, 10, 0, 0))  # Anchor timestamp
   ```
2. Add a Scenario instance to the SCENARIOS dict:
   ```python
   "new_scenario": Scenario(
       name="new_scenario",
       description="Description of the failure mode",
       ground_truth=GroundTruth(
           root_cause_keywords=["keyword1", "keyword2"],
           forbidden_keywords=["misleading1"],  # Optional
           expected_tools=["search_logs", "timeline"],  # Minimum tools needed
           culprit_file="app.log",
           culprit_timestamp=_at(_NEW_SCENARIO_S),
           description="Human-readable explanation of the failure..."
       ),
       generate=_gen_new_scenario,
   ),
   ```
3. The scenario will automatically be available for use in cases

### To Add New Cases
1. Edit `evals/cases.py` to add new EvalCase instances using the `make_case` helper:
   ```python
   # Tool layer case
   make_case(
       case_id="tool-newtool-newsceario",
       layer="tool",
       scenario_name="new_scenario",
       tool_name="newtool",
       tool_input={"param": "value"},
   ),
   
   # Outcome layer case
   make_case(
       case_id="outcome-newsceario",
       layer="outcome",
       scenario_name="new_scenario",
       prompt="What is the root cause of the issue in the logs?",
   ),
   ```
2. Graders will be automatically added based on the scenario's ground truth
3. For trajectory layer cases, you may want to add specific graders:
   ```python
   make_case(
       case_id="traj-newsceario_specific_pattern",
       layer="trajectory",
       scenario_name="new_scenario",
       prompt="Follow this specific tool usage pattern...",
       graders=[
           ToolTrajectoryGrader(expected=["specific_tool1", "specific_tool2"]),
           BrakeGrader(),
       ],
   )
   ```

### To Add New Graders
1. Implement a new Grader subclass in `evals/graders.py`:
   ```python
   class MyNewGrader(Grader):
       name = "my_new_grader"
       
       def grade(self, result: EvalResult, case: EvalCase) -> GraderScore:
           # Implement scoring logic
           passed = bool(...)  # True if passes, False otherwise
           score = float(...)  # 0.0 to 1.0
           detail = "Human-readable explanation"
           return self._score(passed, score, detail)
   ```
2. Add it to cases where appropriate (either in cases.py or by modifying make_case)
3. Ensure it follows the Grader interface and doesn't raise exceptions for merely-wrong answers

## Design Principles

1. **Determinism**: All log files are generated deterministically from a fixed timestamp
   - Ensures reproducible evaluation runs
   - Makes failures reliably attributable to the agent, not the fixture

2. **Isolation**: Each scenario gets its own temporary directory
   - Prevents cross-scenario contamination
   - Ensures clean state for each evaluation

3. **Actionable Feedback**: Graders provide specific failure reasons
   - Rather than just pass/fail, explains why a case failed
   - Makes it easy to identify and fix issues

4. **Progressive Disclosure**: Layers test increasingly complex capabilities
   - Tool layer: Verify basic functionality
   - Trajectory layer: Verify decision-making process
   - Outcome layer: Verify end-to-end diagnostic ability

5. **Composability**: Mix and match scenarios, cases, and graders
   - Reuse scenarios across different case types
   - Apply different grader combinations as needed
   - Build complex evaluations from simple building blocks

## Example: How an Outcome Case Works End-to-End

For `outcome-cascading_failure`:
1. **Setup**:
   - Runner creates temporary directory
   - cascading_failure scenario generates deploy.log, service.log, gateway.log, database.log
   - Ground truth specifies: root_cause_keywords=["DB_POOL_SIZE", "deploy"], expected_tools=["search_logs", "timeline"]

2. **Execution**:
   - Agent receives prompt: "What is the root cause of the 502 errors seen in the gateway logs around 10:03-10:05?"
   - Harness loop begins:
     * Turn 1: 
       - LLM considers prompt and decides to use search_logs tool
       - Harness executes: search_logs({"pattern": "502", "file": "gateway.log"})
       - Tool result returned to LLM
     * Turn 2:
       - LLM sees 502 errors in gateway, decides to search for root cause
       - Harness executes: search_logs({"pattern": "DB_POOL_SIZE", "file": "deploy.log"})
       - Tool result returned to LLM
     * Turn 3:
       - LLM correlates findings and decides to get timeline
       - Harness executes: timeline({"files": [...], "start_time": "...", "end_time": "..."})
       - Tool result returned to LLM
     * Turn 4:
       - LLM synthesizes all information and provides final answer
       - Harness detects stop condition and returns result

3. **Result Collection**:
   - Final answer: "The root cause is the deploy that increased DB_POOL_SIZE from 10 to 100..."
   - Tool calls: [search_logs, search_logs, timeline] with their inputs
   - Turns: 4
   - Duration: measured execution time

4. **Scoring**:
   - KeywordGrader: Checks for "DB_POOL_SIZE" and "deploy" in final answer → 1.0
   - ForbiddenKeywordGrader: Checks that misleading keywords are absent → 1.0
   - ToolTrajectoryGrader: Verifies search_logs and timeline were used → 1.0
   - BrakeGrader: Confirms agent didn't hit max_turns/max_tool_calls unnecessarily → 1.0
   - Overall: Case passed

## Troubleshooting

### Common Issues and Solutions

**Issue**: "Module not found" errors when running evaluations
- **Solution**: Ensure you're running from the project root directory
- **Solution**: Verify dependencies are installed: `pip install -r requirements.txt`

**Issue**: Evaluations take too long or hang
- **Solution**: Check for infinite loops in agent logic
- **Solution**: Use `--verbose` to see where the agent gets stuck
- **Solution**: Consider reducing max_turns/max_tool_calls for debugging

**Issue**: All cases are failing unexpectedly
- **Solution**: Check that scenarios are generating log files correctly
- **Solution**: Verify that the agent can access the log directory
- **Solution**: Check LLM client configuration (especially for NVIDIA API)

**Issue**: ToolTrajectoryGrader failing when it should pass
- **Solution**: Verify expected tools list matches what's in scenario ground truth
- **Solution**: Check that tool names are exactly correct (case-sensitive)
- **Solution**: Use `--verbose` to see exactly what tools were called

**Issue**: KeywordGrader failing despite correct answer
- **Solution**: Check keyword matching is case-insensitive (it should be)
- **Solution**: Verify exact spelling of root cause keywords in ground truth
- **Solution**: Check that final answer isn't empty or malformed

**Issue**: Permission errors when creating temporary directories
- **Solution**: Check write permissions in the current directory
- **Solution**: Try running with elevated privileges if necessary
- **Solution**: Check disk space availability

### Debugging Tips

1. **Use verbose mode**: `python -m evals.runner --nvidia --verbose`
   - Shows every tool call and result
   - Displays LLM inputs and outputs
   - Reveals exactly where the agent deviates from expected behavior

2. **Run specific cases**: `python -m evals.runner --case outcome-cascading_failure --nvidia`
   - Isolates problems to specific scenarios
   - Makes debugging faster and more focused

3. **Test components individually**:
   - Test scenarios: `python -c "from evals.scenarios import *; materialize('cascading_failure', '/tmp/test')"`
   - Test tools directly: Check logagent/logtools.py
   - Test graders: Create manual EvalResult objects and pass to graders

4. **Check the logs**: 
   - Evaluation runs create temporary directories that are cleaned up
   - To preserve logs for inspection, modify runner.py to not clean up on success
   - Or run with a wrapper that preserves the temporary directory

5. **Leverage existing tests**: 
   - The test_harness.py file shows how to properly set up and invoke the harness
   - Similar patterns can be used for debugging evaluation components

## Production Implementation Guide

### When to Use the Full Evaluation Suite
- **Pre-release validation**: Before deploying agent updates to production
- **Continuous integration**: Run evaluations on every code change
- **Regression testing**: Ensure new changes don't break existing capabilities
- **Capacity planning**: Understand agent performance characteristics
- **Coverage verification**: Ensure all failure modes are adequately tested

### When to Use Specific Layers
- **Tool Layer**: 
  - During tool development and debugging
  - When verifying specific tool fixes
  - For rapid feedback on tool correctness

- **Trajectory Layer**:
  - When refining agent decision-making logic
  - When testing specific tool usage patterns
  - For debugging agent's tool selection process

- **Outcome Layer**:
  - Pre-production validation of end-to-end capabilities
  - User acceptance testing
  - Measuring real-world diagnostic performance
  - Benchmarking against baseline agent versions

### Resource Considerations
- **Time**: Outcome layer evaluations take longest (requires LLM calls)
- **Cost**: NVIDIA API usage incurs charges per token
- **Disk space**: Minimal (temporary directories are cleaned up)
- **Memory**: Scales with number of concurrent evaluations (run sequentially by default)

### Best Practices
1. **Start with tool layer**: Ensure basic functionality works before testing complex interactions
2. **Use mock LLM for development**:Fast, free iteration during development
3. **Run outcome layer in CI**: Validate production readiness before deployment
4. **Keep evaluations deterministic**: Ensures reliable, repeatable results
5. **Document evaluation intent**: Each case should clearly state what aspect of the agent it tests
6. **Review failing evaluations**: Treat failed evaluations as valuable debugging information
7. **Maintain evaluation hygiene**: Keep cases and scenarios up-to-date with agent capabilities

## Relationship to the Agent Harness

The evaluation suite is designed to be a comprehensive testing companion to the log agent harness:

- **Harness Focus**: Provides the core agent loop, tool execution, and LLM integration
- **Evaluation Focus**: Provides systematic validation of harness capabilities

Together, they form a complete system:
- The harness enables building agents that can investigate logs
- The evaluation suite ensures those agents work correctly across various scenarios
- Improvements to the harness can be validated with the evaluation suite
- New evaluation scenarios can guide harness enhancements

This separation of concerns allows:
- Harness developers to focus on core functionality and reliability
- Evaluation developers to focus on comprehensive test coverage
- Users to have confidence in the agent's capabilities through rigorous testing

---

*This documentation provides a complete overview of the evaluation suite architecture, components, and usage. For the most up-to-date information, always refer to the source code comments and inline documentation.*