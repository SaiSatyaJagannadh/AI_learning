# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a minimal agent harness tutorial for debugging production logs. The harness demonstrates how an agent loop works by making the `while stop_reason == "tool_use"` pattern explicit rather than hiding it behind framework abstractions.

The project is designed to teach agent fundamentals through clarity over cleverness.

## Core Architecture

The harness follows a simple loop pattern in `logagent/harness.py`:
1. Get completion from LLM
2. Append assistant's content **verbatim** (preserves thinking blocks)
3. If stop_reason is "tool_use": execute tools and append results as a single user message
4. Handle pause_turn and stop conditions
5. Apply brakes (max_turns, max_tool_calls, max_output_tokens)

### Key Components

- **`logagent/harness.py`**: Agent loop with brakes and safety mechanisms. The `AgentHarness.run()` method is the main entry point.
- **`logagent/tools.py`**: `ToolRegistry` for registering and executing tools. Each tool returns `{"content": [...], "is_error": bool}`.
- **`logagent/logtools.py`**: Five purpose-built log debugging tools with output clamping and path safety.
- **`logagent/llm.py`**: LLM client abstraction with `MockClient` (for testing), `NvidiaClient` (production-ready), and `ClaudeClient` (stub).
- **`logagent/transcript.py`**: Verbose logging of every turn, tool call, and result preview.
- **`cli.py`**: Command-line interface that wires everything together.

## Development Commands

### Install Dependencies
```bash
pip install -r requirements.txt
```
Installs: `openai` (for NVIDIA API), `python-dotenv` (for .env), `pytest` (for testing)

### Generate Sample Logs
```bash
python scripts/generate_sample_logs.py
```
Creates four log files in `./logs` describing a cascading failure scenario (deploy → database → service → gateway).

### Run the Agent (Mock Mode)
```bash
python cli.py --initial-prompt "Investigate the 502 errors in the gateway logs" --mock
```
Runs with `MockClient` - no API key needed. The mock follows a fixed plan of tool calls.

### Run with NVIDIA API (Production)
```bash
# Setup: Copy .env.example to .env and add your NVIDIA API key
cp .env.example .env
# Edit .env: NVIDIA_API_KEY=nvapi-your-key-here

# Run the agent
python cli.py --initial-prompt "Why are we seeing 502s?" --nvidia
```
Uses NVIDIA's API with models like `meta/llama-3.1-405b-instruct`. The `NvidiaClient` is fully implemented with function calling support.

Available models:
- `meta/llama-3.1-405b-instruct` (default, most capable)
- `meta/llama-3.1-70b-instruct` (faster, cheaper)
- `nvidia/nemotron-4-340b-instruct`

Override model:
```bash
python cli.py --initial-prompt "Debug this" --nvidia --model meta/llama-3.1-70b-instruct
```

### Run Tests
```bash
python -m pytest tests/test_harness.py -v
```
All tests should pass with no API key and no network traffic.

## Tool Design Principles

The five log tools demonstrate important patterns:

1. **Output clamping with honesty**: Tools must clamp output to a character budget and explicitly state what was held back (e.g., "558 matches, showing 12, 546 not shown — narrow your pattern"). Silent truncation makes the LLM reason over incomplete data.

2. **Error results, not exceptions**: Tool failures return `{"content": [...], "is_error": True}` so the LLM learns from failures. Error messages tell the LLM what went wrong AND what to try next.

3. **Compute for the LLM**: The `timeline` tool merges and sorts logs chronologically - hard work done by the tool, not the LLM.

4. **Path safety**: Every file path is resolved and confined to the log root to prevent path traversal attacks.

5. **Composability**: Use `search_logs` to find interesting timestamps, then `timeline` to see the full picture across multiple logs.

## Harness Safety Mechanisms

### Brakes
- `max_turns`: prevents infinite loops (default: 10)
- `max_tool_calls`: caps total tool invocations (default: 20)
- `max_output_tokens`: limits cumulative LLM tokens (default: 5000)

### Dangerous Tools
- Tools can be marked `dangerous=True` (e.g., file deletion)
- Requires approval via `approve_hook` before execution
- If refused, returns an error result (not an exception)

### Content Integrity
- Assistant's content is appended **verbatim** to preserve thinking blocks
- Multiple tool calls in one turn are collected into a single user message
- Tool results are never silently truncated without telling the LLM

## Adding a New Tool

1. Add method to `LogTools` in `logagent/logtools.py`:
   ```python
   def your_tool(self, args):
       # ... implementation ...
       return {"content": [{"type": "text", "text": result}], "is_error": False}
   ```

2. Register in `cli.py` using `registry.register_tool()` with name, description, JSON Schema parameters, and function reference.

3. Update mock client test plan in `tests/test_harness.py` if testing with mock.

## Key Implementation Details

### Message Format
Messages follow Anthropic's format with content blocks:
```python
{"role": "user"|"assistant", "content": [{"type": "text"|"tool_use"|"tool_result", ...}]}
```

### Tool Result Format
Tools must return:
```python
{
    "content": [{"type": "text", "text": "..."}, ...],
    "is_error": bool  # optional, default False
}
```

### MockClient Testing
The `MockClient` follows a fixed plan but adapts inputs based on previous tool results via `input_adjust` callbacks. Use `mock_client.set_last_tool_result(result)` to feed tool execution results back to the mock.

## When to Use the Anthropic SDK's Tool Runner

Consider using `ToolRunner` or `agentic_loop` from the Anthropic Python SDK when:
- You need production-grade reliability (retries, better error handling)
- You want conversation summarization for complex multi-turn interactions
- You prefer not to manage the loop yourself

This harness is intentionally minimal for teaching purposes. Understanding it first makes you better equipped to use framework solutions later.

## Important Notes

- The harness appends assistant content **verbatim** - thinking blocks survive
- Multiple tool calls in one turn → single user message with all results
- Tool output must be clamped with explicit "N items not shown" messages
- Path traversal protection: all file paths are resolved relative to log_root
- `ClaudeClient` is a stub - implement it using `anthropic.beta.messages.create` with tools
- Never silently truncate tool output - always tell the LLM what was omitted
