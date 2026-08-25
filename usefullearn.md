# Agent Harness: A Complete Learning Guide

## What You'll Learn

This tutorial teaches you **how AI agents actually work** by building a production log debugging agent from scratch. Unlike framework-based approaches that hide the complexity, you'll see every moving part of the agent loop.

By the end, you'll understand:
- How the agent loop pattern works (request → tool use → execution → repeat)
- Why safety mechanisms ("brakes") are critical
- How to design tools that agents can use effectively
- Why output clamping and honest error messages matter
- How to test agents without making API calls

## What Is an Agent?

An **agent** is a language model that can interact with the world through **tools**. The pattern is simple:

1. Give the LLM a task and available tools
2. LLM thinks and decides to use tools
3. Execute the tools and give results back to the LLM
4. Repeat until the LLM is done

The code that manages this loop is called an **agent harness**.

## The 9-Line Agent Loop

At its core, the agent loop is just this:

```python
while self.turn_count < self.max_turns:
    completion = self.llm_client.complete(
        messages=self.messages,
        max_tokens=self.max_output_tokens - self.output_token_count,
    )
    self.messages.append({"role": "assistant", "content": completion["content"]})
    
    if completion["stop_reason"] == "tool_use":
        tool_results = self._execute_tool_calls(completion["content"])
        self.messages.append({"role": "user", "content": tool_results})
    elif completion["stop_reason"] == "pause_turn":
        break
    else:
        break
```

That's it! Everything else is about making this **safe, debuggable, and effective**.

## Why a Harness Instead of Just a Loop?

A harness adds critical features to the basic loop:

### 1. Brakes (Safety Limits)
Without limits, the agent could loop forever or rack up massive costs:
- `max_turns`: Stop after N conversation turns (default: 10)
- `max_tool_calls`: Cap total tool invocations (default: 20)
- `max_output_tokens`: Limit tokens generated (default: 5000)

### 2. Tool Safety
- Tools can be marked `dangerous` (e.g., file deletion)
- Dangerous tools require user approval via an `approve_hook`
- If refused, the tool returns an error (not an exception) so the LLM learns

### 3. Verbose Transcript
Every turn, tool call, and result is logged. Without this, debugging agent behavior is impossible.

### 4. Tool Result Integrity
**Critical concept**: Tools must:
- Clamp output to a character budget
- Explicitly state what was held back (e.g., "558 matches, showing 12, 546 not shown")
- Never silently truncate (the LLM will reason over incomplete data)
- Return errors as results with `is_error: true` (not exceptions)
- Tell the LLM what went wrong AND what to try next

### 5. Path Safety
Every file path is resolved and confined to prevent path traversal attacks.

## The Log Debugging Scenario

This harness has **five purpose-built tools** for debugging production logs:

1. **`list_logs`**: Discover what log files exist
2. **`search_logs`**: Regex search with context lines (clamped output)
3. **`read_log`**: Read specific line ranges (paginated, no "read whole file")
4. **`log_stats`**: Severity counts and time-series histogram
5. **`timeline`**: Merge multiple logs chronologically around a timestamp

The sample logs describe a **cascading failure**:
- `deploy.log`: A config change increases DB_POOL_SIZE from 10 to 100
- `database.log`: Connection refusals (database max_connections is 50)
- `service.log`: Timeouts waiting for the database
- `gateway.log`: 502 errors because the service is down

The agent must correlate events across all four logs to find the root cause.

## Hands-On Tutorial

### Step 1: Generate Sample Logs

```bash
python scripts/generate_sample_logs.py
```

This creates four log files in `./logs/`:
- `deploy.log`: 500 lines, shows the config change
- `database.log`: 800 lines, shows connection refusals
- `service.log`: 700 lines, shows DB timeouts
- `gateway.log`: 600 lines, shows 502 errors

### Step 2: Run the Agent (Mock Mode)

```bash
python cli.py --initial-prompt "Investigate the 502 errors in the gateway logs" --mock
```

**Mock mode** uses a `MockClient` that follows a fixed plan instead of calling the real API. No API key needed!

Watch the transcript:
- Each turn shows what the LLM "thinks" (simulated by the mock)
- Tool calls show the tool name and arguments
- Tool results show what the tool returned (with preview)

The mock will:
1. List available logs
2. Search for "502" in gateway logs
3. Read lines around those errors
4. Check database logs for connection issues
5. Correlate events using the timeline tool
6. Conclude the root cause

### Step 3: Understand the Output

The transcript shows each step:

```
=== Turn 1 ===
Assistant called tool: list_logs
  Arguments: {}
  Result preview: deploy.log (45234 bytes)...

=== Turn 2 ===
Assistant called tool: search_logs
  Arguments: {"pattern": "502", "files": ["gateway.log"]}
  Result preview: gateway.log:102:2026-08-24 10:03:00 ERROR 502 Bad Gateway...
```

The final answer explains the root cause by connecting the dots across logs.

### Step 4: Run the Tests

```bash
python -m pytest tests/test_harness.py -v
```

The tests verify:
- Brakes work correctly (max_turns, max_tool_calls, max_output_tokens)
- Content is appended verbatim (preserves thinking blocks)
- Multiple tool calls in one turn → single user message
- pause_turn handling

All tests use `MockClient` - no API calls, no network traffic.

## Key Design Concepts

### Output Clamping with Honesty

**Bad (silent truncation):**
```python
return {"content": [{"type": "text", "text": output[:500]}]}
```
The LLM doesn't know 90% of the output was cut off!

**Good (honest clamping):**
```python
if len(output) > 500:
    return {
        "content": [{"type": "text", "text": output[:500] + 
                    "\n... 558 matches, showing 12, 546 not shown — narrow your pattern"}]
    }
```
Now the LLM knows to refine the search.

### Error Results, Not Exceptions

**Bad:**
```python
def search_logs(self, args):
    if not args.get("pattern"):
        raise ValueError("pattern required")
```
The exception crashes the agent or gets caught without the LLM learning.

**Good:**
```python
def search_logs(self, args):
    if not args.get("pattern"):
        return {
            "content": [{"type": "text", "text": "Error: 'pattern' is required. Provide a regex pattern to search for."}],
            "is_error": True
        }
```
The LLM sees the error and knows how to fix it.

### Compute for the LLM

The `timeline` tool merges and sorts logs chronologically. This is hard work the tool does instead of asking the LLM to mentally merge timestamps.

**Principle**: Tools should do the heavy lifting. The LLM reasons about the results, not the mechanics.

### Five Tools Beat Twenty

More tools ≠ better agents. These five tools are powerful because:
- They're **composable**: search → find timestamp → timeline around it
- They're **honest**: explicit truncation messages
- They're **safe**: path validation, output clamping
- They're **focused**: each solves one problem well

Adding more tools creates overlap and confusion.

## Project Structure

```
AI_learning/
├── logagent/
│   ├── harness.py        # The agent loop with brakes
│   ├── tools.py          # Tool registry and execution
│   ├── logtools.py       # The five log debugging tools
│   ├── llm.py            # MockClient and ClaudeClient (stub)
│   └── transcript.py     # Verbose logging for debugging
├── scripts/
│   └── generate_sample_logs.py  # Creates the cascading failure scenario
├── tests/
│   └── test_harness.py   # Unit tests using MockClient
├── cli.py                # Command-line interface
└── logs/                 # Generated log files go here
```

## Deep Dive: How MockClient Works

`MockClient` follows a fixed **plan** of tool calls:

```python
plan = [
    {
        "tool": "list_logs",
        "input": {},
        "input_adjust": lambda prev_result: {},
    },
    {
        "tool": "search_logs",
        "input": {"pattern": "502", "files": ["gateway.log"]},
        "input_adjust": lambda prev_result: {},
    },
]
mock_client = MockClient(plan=plan)
```

Each turn:
1. Returns a `tool_use` block with the next planned tool
2. After tool execution, `set_last_tool_result()` updates its state
3. `input_adjust` can modify the next tool's input based on previous results
4. When the plan is exhausted, returns `stop_reason: "stop"` with a final answer

This lets you test the entire agent loop without making API calls or paying for tokens.

## Exercise: Add a New Tool

Try adding a `grep_count` tool that returns match counts instead of actual matches:

**1. Add the method to `LogTools` in `logagent/logtools.py`:**

```python
def grep_count(self, args: Dict[str, Any]) -> Dict[str, Any]:
    """Count matches for a pattern in each log file."""
    pattern = args.get("pattern")
    if not pattern:
        return {
            "content": [{"type": "text", "text": "Error: 'pattern' is required"}],
            "is_error": True,
        }
    
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return {
            "content": [{"type": "text", "text": f"Invalid regex: {str(e)}"}],
            "is_error": True,
        }
    
    # Count matches per file
    counts = {}
    for fname in os.listdir(self.log_root):
        fpath = os.path.join(self.log_root, fname)
        if os.path.isfile(fpath):
            count = 0
            with open(fpath, 'r') as f:
                for line in f:
                    if regex.search(line):
                        count += 1
            counts[fname] = count
    
    # Format output
    lines = [f"{fname}: {count} matches" for fname, count in sorted(counts.items())]
    output = "\n".join(lines) if lines else "No matches found"
    
    return {
        "content": [{"type": "text", "text": output}],
        "is_error": False,
    }
```

**2. Register the tool in `cli.py`:**

```python
registry.register_tool(
    name="grep_count",
    description="Count how many times a regex pattern appears in each log file. Use this to get an overview before searching.",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regular expression pattern to count",
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
    function=log_tools.grep_count,
    dangerous=False,
)
```

**3. Test it:**

Update the mock plan in `tests/test_harness.py` or run with `--mock` after modifying the mock's plan.

## Running in Production with NVIDIA API

The harness includes **full NVIDIA API support** so you can run the agent with real LLMs in production!

**1. Install dependencies:**
```bash
pip install -r requirements.txt
```

This installs:
- `openai` (NVIDIA uses OpenAI-compatible API)
- `python-dotenv` (for loading .env files)
- `pytest` (for testing)

**2. Set up your `.env` file:**

```bash
cp .env.example .env
```

Edit `.env` and add your NVIDIA API key:
```
NVIDIA_API_KEY=nvapi-your-actual-key-here
NVIDIA_MODEL=meta/llama-3.1-405b-instruct
```

Get your NVIDIA API key from: https://build.nvidia.com/

**3. Run with NVIDIA API:**
```bash
python cli.py --initial-prompt "Investigate the 502 errors in the gateway logs" --nvidia
```

The agent will use the real NVIDIA API with function calling (tools) to debug the logs!

**Available Models:**
- `meta/llama-3.1-405b-instruct` (most capable, default)
- `meta/llama-3.1-70b-instruct` (faster, cheaper)
- `nvidia/nemotron-4-340b-instruct` (NVIDIA's own model)

**How It Works:**

The `NvidiaClient` in `logagent/llm.py`:
1. Uses OpenAI SDK with NVIDIA's base URL (`https://integrate.api.nvidia.com/v1`)
2. Converts our message format to OpenAI format
3. Converts our tools to OpenAI function calling format
4. Makes the API call with tool support
5. Converts the response back to our format
6. Handles tool calls and returns them to the harness

This is a **production-ready implementation** - not a stub!

**Alternative: Pass API key via command line:**
```bash
python cli.py --initial-prompt "Find the root cause" --nvidia --api-key nvapi-your-key --model meta/llama-3.1-70b-instruct
```

## Connecting to Real Claude API

The `ClaudeClient` is currently a stub. To implement it, follow a similar pattern to `NvidiaClient` but use the Anthropic SDK:

1. Install the Anthropic SDK:
```bash
pip install anthropic
```

2. Update `llm.py`:
```python
def complete(self, messages, max_tokens, stop_sequences):
    # Convert our message format to Anthropic format
    anthro_messages = []
    for msg in messages:
        role = msg["role"]
        content_blocks = msg.get("content", [])
        # Extract text and tool results
        # ... format conversion ...
    
    # Call the API
    response = self.client.messages.create(
        model=self.model,
        max_tokens=max_tokens,
        messages=anthro_messages,
        tools=[...],  # Convert tool registry to Anthropic tool schemas
        stop_sequences=stop_sequences,
    )
    
    # Convert response to our format
    return {
        "stop_reason": response.stop_reason,
        "content": response.content,
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
    }
```

3. Run with your API key:
```bash
export ANTHROPIC_API_KEY=your-key-here
python cli.py --initial-prompt "Why are we seeing 502s?" --model claude-sonnet-5
```

## Important Patterns to Remember

### 1. Append Content Verbatim
```python
self.messages.append({"role": "assistant", "content": completion["content"]})
```
The `content` is a list of blocks (text, thinking, tool_use). Append it as-is to preserve everything.

### 2. Collect Tool Results in a Single Message
```python
tool_results = []
for tool_call in tool_calls:
    result = execute_tool(tool_call)
    tool_results.append(result)

self.messages.append({"role": "user", "content": tool_results})
```
All tool results from one turn go in one user message.

### 3. Never Silent Truncation
```python
clamped, was_truncated, truncated_chars = _clamp_output(text, budget)
if was_truncated:
    clamped += f"\n... truncated {truncated_chars} chars, total was {len(text)} chars"
```

### 4. Path Validation
```python
def _resolve_path(log_root, user_path):
    full_path = os.path.normpath(os.path.join(log_root, user_path))
    if not full_path.startswith(os.path.normpath(log_root)):
        raise ValueError(f"Path escapes log root")
    return full_path
```

## When to Use Framework Solutions

This harness is for **learning**. In production, consider using:

- **Anthropic SDK's `messages.stream` with tools**: Built-in streaming, retries, better error handling
- **LangChain Agent**: High-level abstraction with many pre-built tools
- **Anthropic Agent SDK**: If you want to build more complex agents with managed state

But understanding this harness first makes you better at using those frameworks.

## Common Pitfalls

### ❌ Infinite Loops
Without brakes, the agent can loop forever. Always set `max_turns` and `max_tool_calls`.

### ❌ Silent Truncation
If you truncate tool output without telling the LLM, it will reason over incomplete data and draw wrong conclusions.

### ❌ Exceptions Instead of Error Results
Exceptions crash the loop. Return error results so the LLM can learn and adapt.

### ❌ Too Many Tools
Each tool is a choice the LLM must consider. Five focused tools beat twenty overlapping ones.

### ❌ Forgetting Path Validation
User-provided paths must be validated to prevent path traversal attacks.

### ❌ No Transcript
Without logging, debugging agent behavior is nearly impossible. Always include a transcript.

## What You've Learned

✅ How the agent loop pattern works  
✅ Why brakes (safety limits) are essential  
✅ How to design tools with output clamping and honest error messages  
✅ Why error results beat exceptions  
✅ How to test agents without API calls using MockClient  
✅ Why path validation matters  
✅ The importance of verbose transcripts for debugging  

## Next Steps

1. **Run the agent**: Generate logs and run with `--mock`
2. **Read the transcript**: See how the agent reasons through the problem
3. **Add your own tool**: Follow the exercise above
4. **Connect to real API**: Implement `ClaudeClient` with the Anthropic SDK
5. **Build your own agent**: Apply these patterns to a different domain

## Resources

- **README.md**: Quick start and overview
- **CLAUDE.md**: Development commands and architecture reference
- **logagent/harness.py**: The core loop implementation
- **logagent/logtools.py**: Example tool implementations
- **tests/test_harness.py**: Testing patterns with MockClient

## Key Takeaway

An agent is just a loop:
1. LLM thinks and chooses tools
2. Execute tools
3. Give results back
4. Repeat

The harness makes this **safe** (brakes), **debuggable** (transcript), and **effective** (honest tool outputs).

Now you understand how agents actually work—not as magic, but as a simple loop with careful engineering around it.

Happy building! 🚀
