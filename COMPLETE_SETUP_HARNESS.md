# Complete Agent Harness Setup & Architecture
**Complete System Documentation - Everything Connected**

## Table of Contents
1. [System Overview](#system-overview)
2. [Complete Architecture](#complete-architecture)
3. [File-by-File Breakdown](#file-by-file-breakdown)
4. [Data Flow](#data-flow)
5. [How Everything Connects](#how-everything-connects)
6. [Production Implementation Guide](#production-implementation-guide)
7. [Troubleshooting](#troubleshooting)

---

## System Overview

This is a **production-ready agent harness** for debugging logs using AI. The agent:
1. Receives a natural language prompt about log issues
2. Chooses appropriate tools to investigate
3. Executes tools to gather information
4. Reasons about the results
5. Returns a diagnosis of the problem

**Key Innovation:** The agent loop is **explicit** (not hidden in a framework), so you can see and control every step.

---

## Complete Architecture

### The Big Picture

```
┌─────────────────────────────────────────────────────────────────┐
│                         USER REQUEST                             │
│  "Investigate the 502 errors in the gateway logs"               │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                          cli.py                                  │
│  • Parse arguments (--nvidia, --max-turns, --initial-prompt)    │
│  • Load .env file (NVIDIA_API_KEY, NVIDIA_MODEL)               │
│  • Create LogTools instance                                     │
│  • Register 5 tools in ToolRegistry                             │
│  • Create LLM client (MockClient OR NvidiaClient)              │
│  • Create AgentHarness with all components                      │
│  • Call harness.run(initial_prompt)                            │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    AgentHarness (harness.py)                    │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                    THE AGENT LOOP                          │  │
│  │                                                             │  │
│  │  while turn_count < max_turns:                            │  │
│  │      1. completion = llm_client.complete(messages)        │  │
│  │      2. messages.append(assistant response)               │  │
│  │      3. if stop_reason == "tool_use":                     │  │
│  │           • Extract tool calls from response              │  │
│  │           • Check brakes (max_tool_calls)                │  │
│  │           • Execute tools via tool_registry               │  │
│  │           • Collect results                               │  │
│  │           • messages.append(tool results)                 │  │
│  │         else:                                             │  │
│  │           • break (agent is done)                        │  │
│  └───────────────────────────────────────────────────────────┘  │
└────────────┬────────────────────────────────┬───────────────────┘
             │                                │
             │ Tool execution                 │ LLM calls
             ▼                                ▼
┌──────────────────────────┐    ┌────────────────────────────────┐
│  ToolRegistry (tools.py) │    │   LLM Clients (llm.py)        │
│  • Stores tool           │    │   ┌──────────────────────────┐ │
│    definitions           │    │   │ NvidiaClient              │ │
│  • Maps name → function  │    │   │ • Uses OpenAI SDK        │ │
│  • Executes tools        │    │   │ • Converts messages      │ │
│  • Returns results       │    │   │ • Converts tools         │ │
│  • Handles errors        │    │   │ • Makes API call         │ │
└──────────┬───────────────┘    │   │ • Parses response        │ │
           │                    │   └──────────────────────────┘ │
           ▼                    │   ┌──────────────────────────┐ │
┌──────────────────────────┐    │   │ MockClient               │ │
│  LogTools (logtools.py)  │    │   │ • Follows fixed plan     │ │
│  ┌──────────────────────┐│    │   │ • No API calls           │ │
│  │ 1. list_logs()       ││    │   │ • For testing            │ │
│  │    • Discover files  ││    │   └──────────────────────────┘ │
│  └──────────────────────┘│    │   ┌──────────────────────────┐ │
│  ┌──────────────────────┐│    │   │ ClaudeClient (stub)      │ │
│  │ 2. search_logs()     ││    │   │ • Not implemented        │ │
│  │    • Regex search    ││    │   └──────────────────────────┘ │
│  │    • With context    ││    └────────────────────────────────┘
│  └──────────────────────┘│                    │
│  ┌──────────────────────┐│                    │
│  │ 3. read_log()        ││                    │
│  │    • Pagination      ││                    ▼
│  │    • Line ranges     ││    ┌────────────────────────────────┐
│  └──────────────────────┘│    │   NVIDIA API                   │
│  ┌──────────────────────┐│    │   https://integrate.api        │
│  │ 4. log_stats()       ││    │          .nvidia.com/v1        │
│  │    • Severity counts ││    │                                │
│  │    • Time histogram  ││    │   Models:                      │
│  └──────────────────────┘│    │   • meta/llama-3.3-70b        │
│  ┌──────────────────────┐│    │   • meta/llama-3.1-70b        │
│  │ 5. timeline()        ││    │   • nvidia/nemotron-*         │
│  │    • Merge logs      ││    └────────────────────────────────┘
│  │    • Chronological   ││
│  └──────────────────────┘│
│  All tools:              │
│  • Clamp output ~500char│
│  • Report truncation    │
│  • Return errors as     │
│    results, not throws  │
│  • Validate paths       │
└──────────┬───────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Sample Logs (logs/)                            │
│  Generated by: scripts/generate_sample_logs.py                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ deploy.log (511 lines)                                    │   │
│  │ • Normal deployment activity                              │   │
│  │ • LINE 100: "Config change: DB_POOL_SIZE 10 → 100"      │   │
│  │ • This is the ROOT CAUSE                                 │   │
│  └──────────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ database.log (874 lines)                                  │   │
│  │ • Lines 1-150: Normal connections                         │   │
│  │ • Lines 151+: "too many connections" errors               │   │
│  │ • Pool size 100 > max_connections 50                     │   │
│  └──────────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ service.log (758 lines)                                   │   │
│  │ • Lines 1-200: Normal request processing                  │   │
│  │ • Lines 201+: "DB query timeout after 5s"                │   │
│  │ • Can't get DB connections, so times out                 │   │
│  └──────────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ gateway.log (666 lines)                                   │   │
│  │ • Lines 1-100: Normal 200 responses                       │   │
│  │ • Lines 101+: "502 Bad Gateway: upstream service timeout"│   │
│  │ • 62 total 502 errors                                     │   │
│  │ • Service is timing out, so gateway returns 502          │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### The Transcript (transcript.py)

Runs **parallel** to the entire flow, logging everything:

```
┌─────────────────────────────────────┐
│   Transcript (transcript.py)        │
│   • Logs every turn start           │
│   • Logs every assistant response   │
│   • Logs every tool call + args     │
│   • Logs every tool result preview  │
│   • Logs stop reasons               │
│   • Logs errors                     │
│   • Prints to stdout                │
└─────────────────────────────────────┘
```

---

## File-by-File Breakdown

### Core Files

#### 1. **cli.py** (260 lines) - Entry Point
**Main function:** `main()`

**What it does:**
```
1. Parse command-line arguments
   • --initial-prompt (required)
   • --nvidia (use NVIDIA API)
   • --mock (use mock client)
   • --max-turns, --max-tool-calls, --max-output-tokens
   • --log-root (default: ./logs)

2. Load environment variables from .env
   • NVIDIA_API_KEY
   • NVIDIA_MODEL

3. Create Transcript instance
   • For verbose logging

4. Create LogTools instance
   • Pass log_root directory
   • Sets output_char_budget (500 chars)

5. Create ToolRegistry
   • Register list_logs
   • Register search_logs
   • Register read_log
   • Register log_stats
   • Register timeline
   • Each with name, description, JSON schema, function reference

6. Create LLM client
   • If --mock: MockClient(plan=[])
   • If --nvidia: NvidiaClient(api_key, model)
   •   Then: client.set_tools(registry.list_tools())
   • Else: ClaudeClient (not implemented)

7. Create AgentHarness
   • Pass transcript, registry, client, brakes

8. Run the agent
   • harness.run(initial_prompt)
   • Print final answer
```

**Key code:**
```python
def main():
    parser = argparse.ArgumentParser()
    # ... argument parsing ...
    
    load_dotenv()  # Load .env file
    
    transcript = Transcript(enabled=not args.no_transcript)
    log_tools = LogTools(log_root=args.log_root)
    registry = ToolRegistry()
    
    # Register all 5 tools
    registry.register_tool("list_logs", description, schema, log_tools.list_logs)
    # ... 4 more tools ...
    
    if args.nvidia:
        llm_client = NvidiaClient(api_key=args.api_key, model=args.model)
        llm_client.set_tools(registry.list_tools())
    
    harness = AgentHarness(transcript, registry, llm_client, ...)
    result = harness.run(args.initial_prompt)
    print(result)
```

---

#### 2. **logagent/harness.py** (251 lines) - The Agent Loop
**Main function:** `run(initial_prompt)`

**What it does:**
```
1. Initialize conversation with user prompt
   messages = [{"role": "user", "content": initial_prompt}]

2. Start the agent loop
   while turn_count < max_turns:
   
3. Get completion from LLM
   completion = llm_client.complete(messages, max_tokens, stop_sequences)
   
4. Update token count (for brake checking)
   
5. Log the assistant's response (via transcript)
   
6. Append assistant content VERBATIM to messages
   messages.append({"role": "assistant", "content": content_blocks})
   
7. Check stop_reason:
   
   IF stop_reason == "tool_use":
      • Extract all tool_use blocks from content
      • Check if total tool calls exceeds max_tool_calls
      • If yes: return error results for each tool
      • If no: execute each tool via tool_registry
      • Check if tool is dangerous and needs approval
      • Collect all tool results
      • Append ALL results as SINGLE user message
      • messages.append({"role": "user", "content": tool_results})
      • Continue loop
      
   ELIF stop_reason == "pause_turn":
      • Break loop (agent paused)
      
   ELSE (stop, max_tokens, etc):
      • Break loop (agent finished)

8. Return the final answer
   • Extract text blocks from last assistant message
   • Concatenate and return
```

**Key code (THE 9-LINE LOOP):**
```python
def run(self, initial_prompt: str) -> str:
    self.messages = [{"role": "user", "content": initial_prompt}]
    
    while self.turn_count < self.max_turns:
        self.turn_count += 1
        
        # Get completion from LLM
        completion = self.llm_client.complete(
            messages=self.messages,
            max_tokens=self.max_output_tokens - self.output_token_count,
            stop_sequences=["\n\nHuman:", "\n\nAssistant:"],
        )
        
        # Append assistant's content VERBATIM
        self.messages.append(
            {"role": "assistant", "content": completion["content"]}
        )
        
        # Handle stop reasons
        if completion["stop_reason"] == "tool_use":
            tool_results = self._execute_tool_calls(completion["content"])
            if tool_results:
                self.messages.append({"role": "user", "content": tool_results})
        elif completion["stop_reason"] == "pause_turn":
            break
        else:
            break
    
    return self._extract_final_answer()
```

**Brakes implemented:**
- `max_turns` - Prevents infinite loops
- `max_tool_calls` - Prevents runaway tool execution
- `max_output_tokens` - Prevents excessive token usage

---

#### 3. **logagent/tools.py** (100 lines) - Tool Registry
**Main function:** `execute_tool(name, tool_input)`

**What it does:**
```
1. Store tool definitions
   • name: string identifier
   • description: what the tool does (for LLM)
   • parameters: JSON Schema for validation
   • function: Python callable
   • dangerous: bool (requires approval)

2. Register new tools via register_tool()
   • Add to internal _tools dict

3. List tools for LLM via list_tools()
   • Returns tool specs without the function reference
   • LLM uses this to decide which tool to call

4. Execute tools via execute_tool(name, input)
   • Look up tool by name
   • If not found: return error result
   • Call the function with input
   • Catch exceptions and return error results
   • Ensure result has correct structure
```

**Key code:**
```python
class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, Dict[str, Any]] = {}
    
    def register_tool(self, name, description, parameters, function, dangerous=False):
        self._tools[name] = {
            "name": name,
            "description": description,
            "parameters": parameters,
            "function": function,
            "dangerous": dangerous,
        }
    
    def execute_tool(self, name, tool_input):
        tool = self._tools.get(name)
        if not tool:
            return {"content": [{"type": "text", "text": f"Tool '{name}' not found"}], "is_error": True}
        
        try:
            result = tool["function"](tool_input)
            # Ensure proper structure
            if "content" not in result:
                result["content"] = []
            if "is_error" not in result:
                result["is_error"] = False
            return result
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}
```

---

#### 4. **logagent/logtools.py** (479 lines) - The 5 Tools
**Main functions:** `list_logs()`, `search_logs()`, `read_log()`, `log_stats()`, `timeline()`

**Common pattern for ALL tools:**
```
1. Extract and validate arguments
2. Convert string parameters to correct types (int())
3. Validate paths (prevent traversal attacks)
4. Perform the operation
5. Clamp output to character budget
6. Report what was truncated
7. Return {"content": [...], "is_error": False/True}
```

**Tool 1: list_logs(args)**
```
Input:  {"pattern": "*"}  # glob pattern, optional
Output: List of files with sizes
Example: "deploy.log (23589 bytes)\ndatabase.log (44913 bytes)..."
```

**Tool 2: search_logs(args)**
```
Input:  {
  "pattern": "502",           # regex (required)
  "files": ["gateway.log"],   # which files (optional, default all)
  "context_lines": 5,         # lines around match (optional)
  "limit": 100                # max matches (optional)
}
Output: Matches with line numbers and context
Example: "gateway.log:109:2026-08-24 ERROR 502 Bad Gateway..."
Special: "... 62 matches shown, 5166 chars not shown"
```

**Tool 3: read_log(args)**
```
Input:  {
  "file": "gateway.log",    # required
  "start_line": 100,         # optional, default 1
  "end_line": 200            # optional, default start+99
}
Output: Lines from the file
Pagination: Reads only requested line range (not whole file)
```

**Tool 4: log_stats(args)**
```
Input:  {"files": ["database.log"]}  # optional, default all
Output: Severity counts (ERROR, WARN, INFO, DEBUG)
        Errors-per-hour histogram
Example: "ERROR: 45\nWARN: 23\n..."
```

**Tool 5: timeline(args)**
```
Input:  {
  "files": ["deploy.log", "database.log"],  # required
  "around": "2026-08-24 10:03:00",          # required timestamp
  "window": "00:05:00",                      # optional, default 5 min
  "limit": 100                               # optional
}
Output: Merged chronological view of all files around timestamp
Purpose: Correlate events across multiple logs
Example: Shows deploy at 10:00, DB errors at 10:03, service errors at 10:05
```

**Key safety features:**
```python
# Path validation (prevents ../../../etc/passwd)
def _resolve_path(log_root, path):
    full_path = os.path.normpath(os.path.join(log_root, path))
    if not full_path.startswith(os.path.normpath(log_root)):
        raise ValueError(f"Path escapes log root")
    return full_path

# Output clamping (prevents context overflow)
def _clamp_output(text, max_chars):
    if len(text) <= max_chars:
        return text, False, 0
    clamped = text[:max_chars]
    # Try to cut at newline
    last_newline = clamped.rfind('\n')
    if last_newline > max_chars * 0.8:
        clamped = text[:last_newline]
    truncated = len(text) - len(clamped)
    return clamped, True, truncated

# Type conversion (handles LLM passing "5" instead of 5)
context_lines = int(args.get("context_lines", 0))
limit = int(args.get("limit", 100))
```

---

#### 5. **logagent/llm.py** (498 lines) - LLM Clients

**Three clients:**

**A. MockClient** - For testing
```
Purpose: Test the agent loop without API calls
How it works:
  • Takes a "plan" - list of tool calls to make
  • Each turn: returns next planned tool_use
  • When plan exhausted: returns stop
  
Use case: Unit tests, CI/CD, development

Example plan:
  [
    {"tool": "search_logs", "input": {"pattern": "502", "files": ["gateway.log"]}},
    {"tool": "read_log", "input": {"file": "database.log", "start_line": 150}}
  ]
```

**B. NvidiaClient** - PRODUCTION (~210 lines)
```
Purpose: Real LLM reasoning with NVIDIA models
Dependencies: openai SDK, python-dotenv

How it works:
  1. Initialize with API key and model
     • Reads from .env: NVIDIA_API_KEY, NVIDIA_MODEL
     • Creates OpenAI client with base_url=https://integrate.api.nvidia.com/v1
  
  2. Store tools for function calling
     • set_tools(tool_list) saves tool schemas
  
  3. complete(messages, max_tokens, stop_sequences)
     • Convert our messages → OpenAI format
       - Extract text blocks
       - Extract tool_use blocks → OpenAI tool_calls format
       - Extract tool_result blocks → OpenAI tool role messages
     
     • Convert our tools → OpenAI function calling format
       - Each tool becomes {"type": "function", "function": {...}}
     
     • Make API call
       - client.chat.completions.create(...)
       - With tools parameter for function calling
     
     • Parse response
       - Extract message.content (text)
       - Extract message.tool_calls (if any)
       - Convert finish_reason → our stop_reason
         * "stop" → "stop"
         * "tool_calls" → "tool_use"
         * "length" → "max_tokens"
     
     • Convert back to our format
       - {"stop_reason": "...", "content": [...], "usage": {...}}
     
  4. Return to harness

Supported models:
  • meta/llama-3.3-70b-instruct (default, tested)
  • meta/llama-3.1-70b-instruct
  • nvidia/llama-3.1-nemotron-70b-instruct
```

**Key code:**
```python
class NvidiaClient(LLMClient):
    def __init__(self, api_key, model):
        from openai import OpenAI
        self.api_key = api_key or os.getenv("NVIDIA_API_KEY")
        self.model = model or os.getenv("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct")
        self.client = OpenAI(
            api_key=self.api_key,
            base_url="https://integrate.api.nvidia.com/v1"
        )
        self.tools = []
    
    def complete(self, messages, max_tokens, stop_sequences):
        # Convert messages
        openai_messages = self._convert_messages_to_openai_format(messages)
        
        # Make API call
        response = self.client.chat.completions.create(
            model=self.model,
            messages=openai_messages,
            max_tokens=max_tokens,
            tools=self._convert_tools_to_openai_format(),
            tool_choice="auto"
        )
        
        # Parse and convert response
        # ... conversion logic ...
        
        return {
            "stop_reason": stop_reason,
            "content": content_blocks,
            "usage": {"input_tokens": ..., "output_tokens": ...}
        }
```

**C. ClaudeClient** - STUB (not implemented)
```
Purpose: Future Anthropic Claude integration
Status: Raises NotImplementedError
How to implement: Follow NvidiaClient pattern but use anthropic SDK
```

---

#### 6. **logagent/transcript.py** (124 lines) - Verbose Logging
**Main functions:** `log_turn_start()`, `log_assistant_turn()`, `log_tool_call()`, etc.

**What it does:**
```
Print every step of the agent loop to stdout:
  • Session start/end
  • Each turn number
  • Assistant responses (thinking, text, tool calls)
  • Tool call arguments (formatted JSON)
  • Tool results (preview, clamped)
  • Errors (marked with !!)
  • Stop reasons

This is ESSENTIAL for:
  • Debugging agent behavior
  • Understanding LLM reasoning
  • Monitoring production runs
  • Learning how agents work
```

**Output format:**
```
============================================================
Session started: agent-session at 2026-08-25 20:25:12
============================================================

--- Turn 1 ---
Assistant:
  -> Tool use: search_logs
     ID: call-80c9e8e2-0082-46e4-8b17-c7058d13416d
     Input: {
  "files": [
    "gateway.log"
  ],
  "pattern": "502"
}
  Stop reason: tool_use
  
  Tool call: search_logs
    Input: {
  "files": [
    "gateway.log"
  ],
  "pattern": "502"
}
    Result: [{'type': 'text', 'text': 'gateway.log:109:...'}]
    
--- Turn 2 ---
Assistant:
  The search revealed 62 instances of 502 errors...
  Stop reason: stop
  
=== Session finished ===
Stop reason: stop
============================================================
```

---

### Supporting Files

#### 7. **scripts/generate_sample_logs.py** (125 lines) - Log Generator
**Main function:** `main()`

**What it does:**
```
Generate 4 log files simulating a cascading failure:

1. deploy.log (511 lines)
   • Lines 1-100: Normal deployment activity
   • Line 100: "Config change: DB_POOL_SIZE increased from 10 to 100"
   • Lines 101-511: More normal activity
   • This is the ROOT CAUSE

2. database.log (874 lines)
   • Lines 1-150: Normal connections accepted
   • Lines 151+: "too many connections" errors
   • Reason: Pool size 100 exceeds database max_connections=50

3. service.log (758 lines)
   • Lines 1-200: Normal request processing (45ms response times)
   • Lines 201+: "DB query timeout after 5s"
   • Reason: Can't get database connections

4. gateway.log (666 lines)
   • Lines 1-100: Normal 200 OK responses
   • Lines 101+: "502 Bad Gateway: upstream service timeout"
   • 62 total 502 errors
   • Reason: Service is timing out

Timeline:
  10:00:00 - Deploy with config change
  10:03:00 - Database connection errors start
  10:05:00 - Service timeout errors start
  10:05:30 - Gateway 502 errors start

The agent's job: Correlate these events and identify the root cause!
```

---

#### 8. **tests/test_harness.py** (299 lines) - Test Suite
**4 test functions:**

**Test 1: `test_harness_brakes_and_verbatim_content()`**
```
What it tests:
  • Brakes work (max_turns, max_tool_calls, max_output_tokens)
  • Content is appended verbatim to messages
  • MockClient follows a plan correctly

How:
  1. Create temporary log directory
  2. Create MockClient with 2-step plan (list_logs, read_log)
  3. Run harness
  4. Assert: Exactly 2 tool calls were made
  5. Assert: Final result contains expected content
```

**Test 2: `test_harness_verbatim_content_appending()`**
```
What it tests:
  • Assistant messages are appended as-is (including thinking blocks)
  • Content structure is preserved

How:
  1. Capture messages passed to LLM
  2. Run harness with mock
  3. Assert: Messages list grows correctly
  4. Assert: Content blocks are preserved
```

**Test 3: `test_harness_multiple_tool_single_user_message()`**
```
What it tests:
  • Multiple tool calls in one turn → single user message
  • Not separate messages for each tool

How:
  1. Run harness (will call multiple tools)
  2. Check message history
  3. Assert: All tool results in one user message
```

**Test 4: `test_harness_pause_turn()`**
```
What it tests:
  • pause_turn stop reason breaks the loop correctly
  • Agent stops gracefully

How:
  1. Create custom mock that returns pause_turn
  2. Run harness
  3. Assert: Loop broke on pause
  4. Assert: Final answer contains expected text
```

Run with: `python -m pytest tests/test_harness.py -v`

---

### Configuration Files

#### 9. **.env** (created by user, gitignored)
```bash
# User creates this from .env.example
NVIDIA_API_KEY=nvapi-your-actual-key-here
NVIDIA_MODEL=meta/llama-3.3-70b-instruct
```

#### 10. **.env.example** (template, tracked in git)
```bash
# Template for .env configuration
NVIDIA_API_KEY=your_nvidia_api_key_here
NVIDIA_MODEL=meta/llama-3.3-70b-instruct
# NVIDIA_MODEL=meta/llama-3.1-70b-instruct
# NVIDIA_MODEL=nvidia/llama-3.1-nemotron-70b-instruct
```

#### 11. **requirements.txt** (dependencies)
```
openai>=1.0.0        # For NVIDIA API (OpenAI-compatible)
python-dotenv>=1.0.0  # For loading .env files
pytest>=7.0.0        # For running tests
```

#### 12. **.gitignore** (security)
```
*.env              # CRITICAL: Protects API keys
__pycache__/       # Python cache
*.pyc              # Compiled Python
logs/              # Generated log files
.pytest_cache/     # Test cache
```

---

## Data Flow

### Complete Request Flow

```
USER
  │
  │ "Investigate the 502 errors"
  │
  ▼
cli.py (main function)
  │
  │ 1. Parse args: --nvidia, --initial-prompt
  │ 2. Load .env: NVIDIA_API_KEY, NVIDIA_MODEL
  │ 3. Create LogTools(log_root="./logs")
  │ 4. Create ToolRegistry()
  │ 5. Register 5 tools → registry
  │ 6. Create NvidiaClient(api_key, model)
  │ 7. client.set_tools(registry.list_tools())
  │ 8. Create AgentHarness(transcript, registry, client, brakes)
  │
  ▼
harness.run("Investigate the 502 errors")
  │
  │ messages = [{"role": "user", "content": "Investigate the 502 errors"}]
  │
  ├─► TURN 1
  │   │
  │   ├─► llm_client.complete(messages)
  │   │   │
  │   │   ├─► NvidiaClient._convert_messages_to_openai_format()
  │   │   │   └─► [{"role": "user", "content": "Investigate..."}]
  │   │   │
  │   │   ├─► NvidiaClient._convert_tools_to_openai_format()
  │   │   │   └─► [{"type": "function", "function": {"name": "search_logs", ...}}, ...]
  │   │   │
  │   │   ├─► client.chat.completions.create(
  │   │   │       model="meta/llama-3.3-70b-instruct",
  │   │   │       messages=[...],
  │   │   │       tools=[...],
  │   │   │       max_tokens=5000
  │   │   │   )
  │   │   │   │
  │   │   │   └─► NVIDIA API (https://integrate.api.nvidia.com/v1)
  │   │   │       │
  │   │   │       ├─► Llama 3.3 70B model reasons:
  │   │   │       │   "I need to search for '502' in gateway.log"
  │   │   │       │
  │   │   │       └─► Returns: {
  │   │   │             "choices": [{
  │   │   │               "message": {
  │   │   │                 "tool_calls": [{
  │   │   │                   "function": {
  │   │   │                     "name": "search_logs",
  │   │   │                     "arguments": '{"files":["gateway.log"],"pattern":"502"}'
  │   │   │                   }
  │   │   │                 }]
  │   │   │               },
  │   │   │               "finish_reason": "tool_calls"
  │   │   │             }]
  │   │   │           }
  │   │   │
  │   │   ├─► NvidiaClient converts response to our format
  │   │   │   └─► {
  │   │   │         "stop_reason": "tool_use",
  │   │   │         "content": [{
  │   │   │           "type": "tool_use",
  │   │   │           "id": "call-80c9e8e2...",
  │   │   │           "name": "search_logs",
  │   │   │           "input": {"files": ["gateway.log"], "pattern": "502"}
  │   │   │         }]
  │   │   │       }
  │   │   │
  │   │   └─► Returns to harness
  │   │
  │   ├─► transcript.log_assistant_turn(content, "tool_use")
  │   │   └─► Prints: "--- Turn 1 ---\nAssistant:\n  -> Tool use: search_logs..."
  │   │
  │   ├─► messages.append({"role": "assistant", "content": [...]})
  │   │   └─► messages = [
  │   │         {"role": "user", "content": "Investigate..."},
  │   │         {"role": "assistant", "content": [{"type": "tool_use", ...}]}
  │   │       ]
  │   │
  │   ├─► stop_reason == "tool_use" → Execute tools
  │   │   │
  │   │   ├─► _execute_tool_calls(content)
  │   │   │   │
  │   │   │   ├─► Extract tool calls: [{"name": "search_logs", "input": {...}}]
  │   │   │   │
  │   │   │   ├─► Check brakes: tool_call_count + 1 <= max_tool_calls? YES
  │   │   │   │
  │   │   │   ├─► tool_registry.execute_tool("search_logs", {"files": [...], "pattern": "502"})
  │   │   │   │   │
  │   │   │   │   ├─► ToolRegistry looks up "search_logs" → finds log_tools.search_logs
  │   │   │   │   │
  │   │   │   │   ├─► log_tools.search_logs({"files": ["gateway.log"], "pattern": "502"})
  │   │   │   │   │   │
  │   │   │   │   │   ├─► Convert params: files=["gateway.log"], pattern="502", limit=100
  │   │   │   │   │   │
  │   │   │   │   │   ├─► Compile regex: re.compile("502")
  │   │   │   │   │   │
  │   │   │   │   │   ├─► Resolve path: /Users/.../logs/gateway.log
  │   │   │   │   │   │
  │   │   │   │   │   ├─► Open file and search:
  │   │   │   │   │   │   for line_num, line in enumerate(file):
  │   │   │   │   │   │       if regex.search(line):
  │   │   │   │   │   │           matches.append({"file": "gateway.log", "line": 109, "match": "..."})
  │   │   │   │   │   │
  │   │   │   │   │   ├─► Found 62 matches
  │   │   │   │   │   │
  │   │   │   │   │   ├─► Format output:
  │   │   │   │   │   │   "gateway.log:109:2026-08-24T10:03:07 ERROR 502 Bad Gateway...\n"
  │   │   │   │   │   │   "gateway.log:118:2026-08-24T10:03:21 ERROR 502 Bad Gateway...\n"
  │   │   │   │   │   │   ...
  │   │   │   │   │   │
  │   │   │   │   │   ├─► Clamp output:
  │   │   │   │   │   │   _clamp_output(output, 500)
  │   │   │   │   │   │   → "... 62 matches shown, 5166 chars not shown"
  │   │   │   │   │   │
  │   │   │   │   │   └─► Return:
  │   │   │   │   │       {
  │   │   │   │   │         "content": [{
  │   │   │   │   │           "type": "text",
  │   │   │   │   │           "text": "gateway.log:109:... 62 matches shown..."
  │   │   │   │   │         }],
  │   │   │   │   │         "is_error": False
  │   │   │   │   │       }
  │   │   │   │   │
  │   │   │   │   └─► Returns to ToolRegistry
  │   │   │   │
  │   │   │   ├─► transcript.log_tool_call("search_logs", input, result)
  │   │   │   │   └─► Prints tool execution details
  │   │   │   │
  │   │   │   └─► Return: [{
  │   │   │         "type": "tool_result",
  │   │   │         "tool_use_id": "call-80c9e8e2...",
  │   │   │         "content": [{"type": "text", "text": "..."}],
  │   │   │         "is_error": False
  │   │   │       }]
  │   │   │
  │   │   └─► Returns to harness
  │   │
  │   ├─► messages.append({"role": "user", "content": tool_results})
  │   │   └─► messages = [
  │   │         {"role": "user", "content": "Investigate..."},
  │   │         {"role": "assistant", "content": [{"type": "tool_use", ...}]},
  │   │         {"role": "user", "content": [{"type": "tool_result", ...}]}
  │   │       ]
  │   │
  │   └─► Continue to Turn 2
  │
  ├─► TURN 2
  │   │
  │   ├─► llm_client.complete(messages)
  │   │   │
  │   │   ├─► Convert messages (now includes tool result)
  │   │   │
  │   │   ├─► API call to NVIDIA
  │   │   │   │
  │   │   │   └─► Llama 3.3 70B sees tool result and reasons:
  │   │   │       "I found 62 instances of 502 errors, all say 'upstream service timeout'.
  │   │   │        The pattern is clear. I can answer now."
  │   │   │
  │   │   ├─► Returns: {
  │   │   │     "stop_reason": "stop",
  │   │   │     "content": [{
  │   │   │       "type": "text",
  │   │   │       "text": "The search revealed 62 instances of 502 errors due to upstream service timeout..."
  │   │   │     }]
  │   │   │   }
  │   │   │
  │   │   └─► Returns to harness
  │   │
  │   ├─► transcript.log_assistant_turn(content, "stop")
  │   │
  │   ├─► messages.append({"role": "assistant", "content": [...]})
  │   │
  │   ├─► stop_reason == "stop" → Break loop
  │   │
  │   └─► Loop ends
  │
  ├─► Extract final answer from last assistant message
  │   └─► "The search revealed 62 instances of 502 errors due to upstream service timeout..."
  │
  └─► Return to cli.py

cli.py receives final answer
  │
  ├─► Print: "Agent finished."
  ├─► Print: "Final answer:"
  ├─► Print: final_answer
  │
  └─► Exit
```

### Message History Evolution

```
Initial state:
messages = [
  {"role": "user", "content": "Investigate the 502 errors"}
]

After Turn 1 (LLM wants to use tool):
messages = [
  {"role": "user", "content": "Investigate the 502 errors"},
  {"role": "assistant", "content": [
    {"type": "tool_use", "id": "call-123", "name": "search_logs", "input": {...}}
  ]}
]

After Tool Execution:
messages = [
  {"role": "user", "content": "Investigate the 502 errors"},
  {"role": "assistant", "content": [{"type": "tool_use", ...}]},
  {"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "call-123", "content": [...]}
  ]}
]

After Turn 2 (LLM finishes):
messages = [
  {"role": "user", "content": "Investigate the 502 errors"},
  {"role": "assistant", "content": [{"type": "tool_use", ...}]},
  {"role": "user", "content": [{"type": "tool_result", ...}]},
  {"role": "assistant", "content": [
    {"type": "text", "text": "The search revealed 62 instances..."}
  ]}
]

Final answer extracted from last assistant message.
```

---

## How Everything Connects

### The Connection Map

```
cli.py
  │
  ├─► Creates LogTools ───┐
  │                        │
  ├─► Creates ToolRegistry ├─► Registers 5 tools
  │                        │   (each tool is a LogTools method)
  │                        │
  ├─► Creates NvidiaClient ├─► Receives tool list from registry
  │                        │
  └─► Creates AgentHarness ├─► Receives:
                           │   • transcript (for logging)
                           │   • registry (for executing tools)
                           │   • client (for LLM calls)
                           │   • brakes (safety limits)
                           │
harness.run()              │
  │                        │
  ├─► Loop starts ─────────┤
  │                        │
  ├─► client.complete() ───┼─► NVIDIA API
  │                        │   └─► Llama model reasons
  │                        │       └─► Returns tool_use
  │                        │
  ├─► registry.execute() ──┼─► Calls LogTools method
  │                        │   └─► Reads log files
  │                        │       └─► Returns results
  │                        │
  └─► Loop continues ──────┘
      or breaks (stop)
```

### The Dependency Graph

```
                    ┌─────────────┐
                    │   cli.py    │
                    └──────┬──────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
        ▼                  ▼                  ▼
   ┌─────────┐      ┌────────────┐    ┌──────────┐
   │LogTools │      │ToolRegistry│    │NvidiaClient│
   └────┬────┘      └──────┬─────┘    └─────┬────┘
        │                  │                  │
        │                  │                  │
        └──────────────────┼──────────────────┘
                           │
                           ▼
                    ┌──────────────┐
                    │AgentHarness  │
                    └──────┬───────┘
                           │
                           ▼
                    ┌──────────────┐
                    │ Transcript   │
                    └──────────────┘

LogTools depends on:
  • os, re, glob, datetime (stdlib)
  • log files in log_root directory

ToolRegistry depends on:
  • LogTools (holds references to tool methods)

NvidiaClient depends on:
  • openai SDK
  • python-dotenv
  • .env file (NVIDIA_API_KEY)
  • NVIDIA API (internet connection)

AgentHarness depends on:
  • ToolRegistry (execute tools)
  • NvidiaClient (get completions)
  • Transcript (logging)

Transcript depends on:
  • sys (stdout)
  • json (formatting)
```

---

## Production Implementation Guide

### Prerequisites

1. **Python 3.11+**
2. **NVIDIA API Key** from https://build.nvidia.com/
3. **Git** (for cloning)

### Setup Steps

#### Step 1: Clone and Install
```bash
git clone https://github.com/SaiSatyaJagannadh/AI_learning.git
cd AI_learning
pip install -r requirements.txt
```

#### Step 2: Configure Environment
```bash
cp .env.example .env
nano .env  # or any editor
```

Edit `.env`:
```bash
NVIDIA_API_KEY=nvapi-your-actual-key-here
NVIDIA_MODEL=meta/llama-3.3-70b-instruct
```

#### Step 3: Generate Sample Logs (for testing)
```bash
python scripts/generate_sample_logs.py
```

#### Step 4: Test with Sample Data
```bash
python3 cli.py --initial-prompt "Find all 502 errors in gateway.log" --nvidia --max-turns 3
```

Expected output:
```
Starting agent with initial prompt: Find all 502 errors in gateway.log
Using NVIDIA LLM client
Model: meta/llama-3.3-70b-instruct

--- Turn 1 ---
Assistant:
  -> Tool use: search_logs
  ...

Final answer:
Found 62 instances of 502 Bad Gateway errors due to upstream service timeout.
```

### Production Deployment

#### Option 1: Command-Line Tool

**Use case:** Ad-hoc log investigations

Deploy:
```bash
# Add to PATH
echo 'export PATH=$PATH:/path/to/AI_learning' >> ~/.bashrc
echo 'alias logagent="python3 /path/to/AI_learning/cli.py"' >> ~/.bashrc
source ~/.bashrc

# Use anywhere
logagent --initial-prompt "Why are we seeing errors?" --nvidia --log-root /var/log/myapp
```

#### Option 2: Python Library

**Use case:** Import into other Python projects

```python
from logagent.harness import AgentHarness
from logagent.tools import ToolRegistry
from logagent.logtools import LogTools
from logagent.llm import NvidiaClient
from logagent.transcript import Transcript

# Set up components
log_tools = LogTools(log_root="/var/log/myapp")
registry = ToolRegistry()
# Register tools...
client = NvidiaClient(api_key="your-key", model="meta/llama-3.3-70b-instruct")
transcript = Transcript(enabled=True)

# Create and run harness
harness = AgentHarness(transcript, registry, client, max_turns=10)
result = harness.run("What's causing the errors?")
print(result)
```

#### Option 3: API Server

**Use case:** Expose as web API

Create `server.py`:
```python
from flask import Flask, request, jsonify
from logagent.harness import AgentHarness
# ... other imports ...

app = Flask(__name__)

@app.route('/investigate', methods=['POST'])
def investigate():
    prompt = request.json['prompt']
    log_root = request.json.get('log_root', './logs')
    max_turns = request.json.get('max_turns', 10)
    
    # Set up harness (reuse instances for performance)
    log_tools = LogTools(log_root=log_root)
    # ... setup ...
    
    result = harness.run(prompt)
    return jsonify({"answer": result})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
```

Deploy:
```bash
pip install flask
python server.py
```

Use:
```bash
curl -X POST http://localhost:5000/investigate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Why are we seeing 502 errors?", "log_root": "/var/log/myapp"}'
```

#### Option 4: Scheduled Jobs

**Use case:** Periodic log analysis

Create `cron_job.py`:
```python
import os
from datetime import datetime
from logagent.harness import AgentHarness
# ... imports ...

def daily_log_analysis():
    # Set up harness
    harness = ...
    
    # Run investigation
    result = harness.run(
        f"Analyze logs from the past 24 hours. "
        f"Report any anomalies or patterns."
    )
    
    # Save report
    date = datetime.now().strftime("%Y-%m-%d")
    with open(f"/reports/log_analysis_{date}.txt", "w") as f:
        f.write(result)
    
    # Alert if issues found
    if "error" in result.lower() or "issue" in result.lower():
        send_alert(result)

if __name__ == '__main__':
    daily_log_analysis()
```

Schedule with cron:
```bash
0 2 * * * /usr/bin/python3 /path/to/cron_job.py
```

### Production Best Practices

#### 1. **Error Handling**
```python
try:
    result = harness.run(prompt)
except Exception as e:
    logger.error(f"Agent harness failed: {e}")
    result = "Investigation failed. Please check manually."
```

#### 2. **Timeout Protection**
```python
import signal

def timeout_handler(signum, frame):
    raise TimeoutError("Agent took too long")

signal.signal(signal.SIGALRM, timeout_handler)
signal.alarm(300)  # 5 minute timeout

try:
    result = harness.run(prompt)
finally:
    signal.alarm(0)  # Cancel alarm
```

#### 3. **Cost Tracking**
```python
class CostTracker:
    def __init__(self):
        self.total_tokens = 0
        self.total_cost = 0.0
    
    def track_completion(self, usage):
        input_tokens = usage.get('input_tokens', 0)
        output_tokens = usage.get('output_tokens', 0)
        # NVIDIA pricing (example rates)
        cost = (input_tokens * 0.002 / 1000) + (output_tokens * 0.006 / 1000)
        self.total_tokens += input_tokens + output_tokens
        self.total_cost += cost

tracker = CostTracker()
# Wrap client.complete() to track usage
```

#### 4. **Rate Limiting**
```python
from time import time, sleep

class RateLimiter:
    def __init__(self, max_calls_per_minute=10):
        self.max_calls = max_calls_per_minute
        self.calls = []
    
    def wait_if_needed(self):
        now = time()
        self.calls = [t for t in self.calls if now - t < 60]
        if len(self.calls) >= self.max_calls:
            sleep(60 - (now - self.calls[0]))
        self.calls.append(time())

limiter = RateLimiter(max_calls_per_minute=10)
# Call limiter.wait_if_needed() before each API call
```

#### 5. **Logging and Monitoring**
```python
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/agent_harness.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger('agent_harness')

# Log key events
logger.info(f"Agent started: {prompt}")
logger.info(f"Turn {turn_count}: {tool_name} called")
logger.info(f"Agent finished: {result[:100]}...")
```

#### 6. **Security Hardening**
```python
# Path validation (already in LogTools)
def validate_log_root(log_root):
    # Only allow specific directories
    allowed_roots = ['/var/log/myapp', '/opt/logs']
    if not any(log_root.startswith(root) for root in allowed_roots):
        raise ValueError(f"Invalid log root: {log_root}")

# API key rotation
def load_api_key():
    # Read from secrets manager instead of .env
    import boto3
    client = boto3.client('secretsmanager')
    secret = client.get_secret_value(SecretId='nvidia-api-key')
    return secret['SecretString']

# Input sanitization
def sanitize_prompt(prompt):
    # Prevent prompt injection
    if len(prompt) > 1000:
        raise ValueError("Prompt too long")
    if any(char in prompt for char in ['<', '>', '{', '}']):
        raise ValueError("Invalid characters in prompt")
    return prompt
```

### Scaling Considerations

#### Horizontal Scaling
```
┌─────────────┐
│Load Balancer│
└──────┬──────┘
       │
   ┌───┴───┬───────┬───────┐
   │       │       │       │
   ▼       ▼       ▼       ▼
┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐
│API  │ │API  │ │API  │ │API  │
│Srv 1│ │Srv 2│ │Srv 3│ │Srv 4│
└─────┘ └─────┘ └─────┘ └─────┘
```

Each server runs the harness independently.
No shared state needed (stateless).

#### Queue-Based Processing
```
Requests → Queue (Redis/RabbitMQ) → Worker Pool → Results DB
                                      ↓
                                    Workers run harness
```

Benefits:
- Decouple request from processing
- Handle spikes in traffic
- Retry failed investigations
- Track job status

#### Caching
```python
import functools
import hashlib

def cache_tool_results(ttl_seconds=300):
    cache = {}
    
    def decorator(func):
        @functools.wraps(func)
        def wrapper(args):
            # Create cache key from tool args
            key = hashlib.md5(str(args).encode()).hexdigest()
            if key in cache:
                cached_result, cached_time = cache[key]
                if time.time() - cached_time < ttl_seconds:
                    return cached_result
            
            result = func(args)
            cache[key] = (result, time.time())
            return result
        return wrapper
    return decorator

# Apply to tools
log_tools.search_logs = cache_tool_results()(log_tools.search_logs)
```

---

## Troubleshooting

### Common Issues

#### 1. "NVIDIA API call failed: 404"
**Cause:** Model name not available

**Solution:**
```bash
# List available models
python3 -c "
from openai import OpenAI
import os
from dotenv import load_dotenv
load_dotenv()
client = OpenAI(api_key=os.getenv('NVIDIA_API_KEY'), base_url='https://integrate.api.nvidia.com/v1')
for model in client.models.list().data:
    print(model.id)
"

# Update .env with valid model
NVIDIA_MODEL=meta/llama-3.3-70b-instruct
```

#### 2. "NameError: name 'json' is not defined"
**Cause:** Missing import in transcript.py

**Solution:** Already fixed in current version. Update from repo.

#### 3. "unsupported operand type(s) for -: 'int' and 'str'"
**Cause:** LLM passed string numbers ("5" instead of 5)

**Solution:** Already fixed in current version. Update from repo.

#### 4. "AttributeError: module aiohttp has no attribute SocketTimeoutError"
**Cause:** Incompatible package versions

**Solution:**
```bash
pip install --upgrade openai aiohttp
# Current working versions: openai 3.3.1, aiohttp 3.14.3
```

#### 5. "Path escapes log root"
**Cause:** Tool trying to access files outside log_root (security feature working correctly)

**Solution:** Ensure log files are in the specified log_root directory.

#### 6. Agent loops forever
**Cause:** No brakes set or LLM stuck in pattern

**Solution:**
```bash
# Add stricter brakes
python3 cli.py --initial-prompt "..." --nvidia --max-turns 5 --max-tool-calls 10
```

#### 7. "Tool 'X' not found"
**Cause:** Tool not registered in cli.py

**Solution:** Check that all 5 tools are registered before harness.run()

#### 8. API timeout
**Cause:** NVIDIA API slow or down

**Solution:**
```python
# Add timeout to API call
import signal

def timeout_handler(signum, frame):
    raise TimeoutError()

signal.signal(signal.SIGALRM, timeout_handler)
signal.alarm(30)  # 30 second timeout
try:
    response = client.chat.completions.create(...)
finally:
    signal.alarm(0)
```

---

## Summary

### The Complete System in One Paragraph

A user runs `cli.py` with a prompt about log issues. The CLI loads the NVIDIA API key from `.env`, creates 5 log debugging tools, registers them in a ToolRegistry, creates a NvidiaClient, and initializes an AgentHarness with all components. The harness runs a loop: it sends messages to the NVIDIA API (Llama model), the model decides to use tools, the harness executes those tools via the registry (which calls LogTools methods to read actual log files), collects results, adds them to the conversation, and loops again. The Transcript logs every step. The loop continues until the model returns `stop_reason: "stop"` or a brake is hit. The final answer is extracted and returned to the user.

### Key Files and Their One-Liner Purpose

| File | One-Liner Purpose |
|------|-------------------|
| **cli.py** | Parse args, load config, wire components, run harness, print result |
| **logagent/harness.py** | The 9-line agent loop: LLM → tools → results → repeat until done |
| **logagent/tools.py** | Store tool definitions, map names to functions, execute with error handling |
| **logagent/logtools.py** | 5 tools that read logs safely with clamping, validation, and honest truncation |
| **logagent/llm.py** | LLM clients: MockClient (testing), NvidiaClient (production), ClaudeClient (stub) |
| **logagent/transcript.py** | Print every turn, tool call, and result to stdout for debugging |
| **scripts/generate_sample_logs.py** | Create 4 log files simulating a cascading failure for testing |
| **tests/test_harness.py** | 4 tests verifying brakes, verbatim content, tool execution, pause_turn |
| **.env** | Store NVIDIA_API_KEY and NVIDIA_MODEL (gitignored, user creates) |
| **requirements.txt** | List dependencies: openai, python-dotenv, pytest |

### Production Deployment Checklist

- [ ] Clone repository
- [ ] Install dependencies (`pip install -r requirements.txt`)
- [ ] Create `.env` with NVIDIA API key
- [ ] Test with sample logs (`python scripts/generate_sample_logs.py`)
- [ ] Run test investigation (`python3 cli.py --initial-prompt "..." --nvidia`)
- [ ] Verify output and transcript
- [ ] Set up error handling and logging
- [ ] Configure rate limiting and timeouts
- [ ] Add monitoring and alerting
- [ ] Deploy as CLI tool, library, API server, or scheduled job
- [ ] Document custom tools if added
- [ ] Set up log rotation for agent logs
- [ ] Configure secret management for API keys
- [ ] Test with production log data
- [ ] Monitor costs and set budget alerts

---

**End of Complete Setup and Architecture Document**

**Total Lines:** ~1,600  
**Total Words:** ~6,000  
**Documented source:** 2,136 lines across 9 Python files  

This document covers everything: how it works, how files connect, how data flows, how to deploy to production, and how to troubleshoot issues. Every file explained, every function documented, every connection mapped.
