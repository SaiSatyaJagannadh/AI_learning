# Getting Started: Understanding the Agent Harness

This guide will walk you through understanding how the agent harness works by running it step-by-step and explaining what you see.

## Prerequisites

Make sure you have:
1. Python 3.11+ installed
2. Dependencies installed: `pip install -r requirements.txt`
3. Your NVIDIA API key in `.env` file
4. Sample logs generated

## Step 1: Generate the Sample Logs

First, create the cascading failure scenario in the logs:

```bash
python scripts/generate_sample_logs.py
```

**What to expect:**
```
Generating sample logs in ./logs...
  deploy.log: 511 lines, 23589 bytes
  database.log: 874 lines, 44913 bytes
  service.log: 758 lines, 39455 bytes
  gateway.log: 666 lines, 34250 bytes
Done.
```

**What this creates:**
- `deploy.log` - Shows a config change (DB_POOL_SIZE increased from 10 to 100)
- `database.log` - Shows connection refusals due to exceeding max connections
- `service.log` - Shows timeouts waiting for database responses
- `gateway.log` - Shows 502 errors because service is timing out

This is a **cascading failure**: one change causes problems that ripple through the entire system.

## Step 2: Run with Mock Client (No API Key Needed)

The mock client follows a fixed plan, so you can see how the agent loop works without making API calls:

```bash
python cli.py --initial-prompt "Investigate the 502 errors" --mock
```

**What to expect:**
```
Starting agent with initial prompt: Investigate the 502 errors
Log root: ./logs
Using mock LLM client

============================================================
Session started: agent-session at 2026-08-25 20:25:12
============================================================

--- Turn 1 ---
Assistant:
  I've completed the investigation. No issues found.
  Stop reason: stop
```

**Why is this simple?**
The mock client has an **empty plan** by default (no tool calls planned). It immediately returns a "stop" response. This is intentional - the mock is designed for testing with custom plans (see `tests/test_harness.py` for examples).

**Key takeaway:** You see the basic loop structure: Turn → Response → Stop reason

## Step 3: Run with NVIDIA API (Real LLM)

Now run with a real LLM that can reason and choose tools:

```bash
python3 cli.py --initial-prompt "Find all 502 errors in gateway.log" --nvidia --max-turns 3
```

**What to expect:**

### Turn 1: LLM decides to use a tool
```
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
```

**What's happening:**
1. The LLM received your prompt: "Find all 502 errors in gateway.log"
2. It looked at the available tools (list_logs, search_logs, read_log, log_stats, timeline)
3. It chose `search_logs` because that's the best tool for finding patterns
4. It generated the arguments: which file and what pattern to search
5. Stop reason is `tool_use` - the agent loop continues

### Tool Execution
```
  Tool call: search_logs
    Input: {
  "files": [
    "gateway.log"
  ],
  "pattern": "502"
}
    Result: [{'type': 'text', 'text': 'gateway.log:109:2026-08-24T10:03:07...
```

**What's happening:**
1. The harness executes `search_logs` with those arguments
2. The tool searches gateway.log for "502"
3. It finds 62 matches and returns the first few with line numbers
4. The result is added to the conversation as a "user" message

### Turn 2: LLM analyzes the results
```
--- Turn 2 ---
Assistant:
  The search for 502 errors in the gateway.log file revealed a pattern of 
  "upstream service timeout" errors, which occurred 62 times...
  Stop reason: stop
```

**What's happening:**
1. The LLM sees the tool results
2. It analyzes the pattern (all errors say "upstream service timeout")
3. It provides an answer
4. Stop reason is `stop` - the agent loop ends

### Final Answer
```
Agent finished.
============================================================
Final answer:
The search for 502 errors in the gateway.log file revealed a pattern...
```

**What's happening:**
The harness extracts the final text response and presents it as the answer.

## Step 4: Run a Deeper Investigation

Let's see the agent reason across multiple log files:

```bash
python3 cli.py --initial-prompt "Investigate why we're seeing 502 errors. Check all logs to find the root cause of this cascading failure." --nvidia --max-turns 8
```

**What to expect (multiple turns):**

### Turn 1: Search gateway logs
```
--- Turn 1 ---
  -> Tool use: search_logs
     Input: {"files": ["gateway.log"], "pattern": "502"}
  Result: Found 62 matches of "502 Bad Gateway: upstream service timeout"
```

### Turn 2: Check service logs
```
--- Turn 2 ---
  -> Tool use: search_logs
     Input: {"files": ["service.log"], "pattern": "timeout"}
  Result: Found timeouts waiting for database
```

### Turn 3: Check database logs
```
--- Turn 3 ---
  -> Tool use: search_logs
     Input: {"files": ["database.log"], "pattern": "connection|error"}
  Result: Found "too many connections" errors
```

### Turn 4: Check deploy logs
```
--- Turn 4 ---
  -> Tool use: search_logs
     Input: {"files": ["deploy.log"], "pattern": "config|change"}
  Result: Found "DB_POOL_SIZE increased from 10 to 100"
```

### Turn 5: Correlate with timeline
```
--- Turn 5 ---
  -> Tool use: timeline
     Input: {
       "files": ["deploy.log", "database.log", "service.log", "gateway.log"],
       "around": "2026-08-24 10:03:00"
     }
  Result: Shows chronological order of events
```

### Final Turn: Conclusion
```
--- Turn 6 ---
The root cause is a config change that increased DB_POOL_SIZE to 100,
but the database has max_connections=50. This caused connection refusals,
which led to service timeouts, which led to gateway 502 errors.
```

## Understanding the Agent Loop

Here's what happens in each iteration:

```
┌─────────────────────────────────────┐
│ 1. LLM receives messages            │
│    (conversation history so far)    │
└────────────┬────────────────────────┘
             │
             ▼
┌─────────────────────────────────────┐
│ 2. LLM generates response           │
│    - Thinks about the problem       │
│    - Decides what to do next        │
│    - May call tools or give answer  │
└────────────┬────────────────────────┘
             │
             ▼
        stop_reason?
             │
    ┌────────┴────────┐
    │                 │
    ▼                 ▼
"tool_use"         "stop"
    │                 │
    ▼                 │
┌─────────────────┐   │
│ 3. Execute      │   │
│    tools        │   │
└────────┬────────┘   │
         │            │
         ▼            │
┌─────────────────┐   │
│ 4. Collect      │   │
│    results      │   │
└────────┬────────┘   │
         │            │
         ▼            │
┌─────────────────┐   │
│ 5. Add results  │   │
│    to messages  │   │
└────────┬────────┘   │
         │            │
         └────────┐   │
                  │   │
                  ▼   ▼
            ┌─────────────┐
            │ Loop again  │
            │ or Stop     │
            └─────────────┘
```

## Key Concepts to Understand

### 1. **Messages List**
The conversation history that grows with each turn:
```python
messages = [
    {"role": "user", "content": "Find 502 errors"},
    {"role": "assistant", "content": [{"type": "tool_use", "name": "search_logs", ...}]},
    {"role": "user", "content": [{"type": "tool_result", ...}]},
    {"role": "assistant", "content": "Here's what I found..."},
]
```

### 2. **Stop Reasons**
- `tool_use` - LLM wants to call tools, loop continues
- `stop` - LLM is done, loop ends
- `max_tokens` - Hit token limit (rare with our settings)
- `pause_turn` - LLM wants to pause (not used in production)

### 3. **Brakes (Safety Limits)**
- `max_turns` - Max iterations of the loop (default: 10)
- `max_tool_calls` - Max total tool invocations (default: 20)
- `max_output_tokens` - Max tokens the LLM can generate (default: 5000)

Without these, the agent could loop forever or rack up huge costs!

### 4. **Tool Results Format**
Every tool returns:
```python
{
    "content": [{"type": "text", "text": "result here"}],
    "is_error": False  # or True if something went wrong
}
```

If `is_error` is True, the LLM sees the error and can try something different.

### 5. **Output Clamping**
Tools limit their output to ~500 characters by default and tell the LLM what was cut:
```
gateway.log:109: ERROR 502 Bad Gateway...
... 62 matches shown, 62 total matches, 5166 chars not shown
```

This prevents overwhelming the LLM with too much data.

## Exploring the Code

### Read these files in order:

**1. `cli.py`** - Start here
- See how tools are registered
- See how the LLM client is created
- See how the harness is initialized
- See how `harness.run()` is called

**2. `logagent/harness.py`** - The core loop
- Read the `run()` method - it's the 9-line loop!
- See how it checks stop_reason
- See how it executes tools
- See how it applies brakes

**3. `logagent/tools.py`** - Tool registry
- See how tools are stored
- See how `execute_tool()` works
- See how errors are handled

**4. `logagent/logtools.py`** - The actual tools
- Read `search_logs()` - most commonly used
- See how output is clamped
- See how errors are returned as results (not exceptions!)

**5. `logagent/llm.py`** - LLM clients
- Read `NvidiaClient.complete()` to see the API call
- See how messages are converted between formats
- See how tool calls are converted

**6. `logagent/transcript.py`** - Logging
- See what gets printed at each step
- This is essential for debugging

## Common Experiments to Try

### Experiment 1: Limit the turns
```bash
python3 cli.py --initial-prompt "Find 502 errors" --nvidia --max-turns 1
```
**Expected:** Agent only gets one turn, may not finish investigation

### Experiment 2: Limit tool calls
```bash
python3 cli.py --initial-prompt "Investigate all logs" --nvidia --max-tool-calls 2
```
**Expected:** Agent stops after 2 tool calls, even if not done

### Experiment 3: Try different prompts
```bash
# Vague prompt - see how agent explores
python3 cli.py --initial-prompt "Something is wrong with the logs" --nvidia

# Specific prompt - see focused investigation  
python3 cli.py --initial-prompt "What changed in deploy.log around 10:00?" --nvidia

# Multi-step prompt - see complex reasoning
python3 cli.py --initial-prompt "Find when errors started, then check what changed before that" --nvidia
```

### Experiment 4: Watch the transcript
The transcript shows every step. Look for:
- What tools the LLM chooses
- What arguments it passes
- How it reasons from results
- When it decides to stop

### Experiment 5: Try different models
Edit `.env` to change models:
```bash
NVIDIA_MODEL=meta/llama-3.1-70b-instruct  # Faster, slightly less capable
NVIDIA_MODEL=nvidia/llama-3.1-nemotron-70b-instruct  # NVIDIA's tuned version
```

## Understanding the Test Suite

Look at `tests/test_harness.py`:

**Test 1:** `test_harness_brakes_and_verbatim_content`
- Shows how MockClient works with a plan
- Shows how brakes prevent infinite loops
- Shows how content is appended verbatim

**Test 2:** `test_harness_pause_turn`
- Shows how pause_turn stops the loop
- Shows custom mock behavior

Run the tests:
```bash
python -m pytest tests/test_harness.py -v
```

**Expected output:**
```
test_harness_brakes_and_verbatim_content PASSED
test_harness_verbatim_content_appending PASSED
test_harness_multiple_tool_single_user_message PASSED
test_harness_pause_turn PASSED

4 passed in 0.02s
```

## What Makes This a "Harness"?

Compare these two:

### Just a loop (not a harness):
```python
while True:
    response = llm.complete(messages)
    if response.stop_reason == "stop":
        break
```

### A harness (this project):
```python
while turn_count < max_turns:  # 🔒 Brake
    response = llm.complete(messages, max_tokens=remaining)  # 🔒 Brake
    messages.append({"role": "assistant", "content": response.content})  # 📝 Verbatim
    
    if response.stop_reason == "tool_use":
        if tool_call_count >= max_tool_calls:  # 🔒 Brake
            break
        results = execute_tools(response.content)  # 🛠️ Safe execution
        messages.append({"role": "user", "content": results})  # 📨 Single message
        transcript.log(results)  # 📊 Debugging
    else:
        break
```

The harness adds:
- 🔒 Brakes (safety limits)
- 📝 Verbatim content appending (preserves thinking)
- 🛠️ Safe tool execution (returns errors, not exceptions)
- 📨 Message batching (all tool results in one message)
- 📊 Verbose transcript (debugging)
- 🔐 Path validation (security)
- ⚠️ Output clamping with honesty (prevents incomplete data)

## Next Steps

1. **Run the agent** with different prompts and watch the transcript
2. **Read the code** in the order suggested above
3. **Modify a tool** in `logtools.py` and see how it affects the agent
4. **Add a new tool** following the exercise in `usefullearn.md`
5. **Experiment with brakes** to see how they affect behavior
6. **Try different models** to compare reasoning ability

## Common Questions

**Q: Why does the agent sometimes stop early?**
A: The LLM decides it has enough information to answer. You can encourage deeper investigation with prompts like "Check all logs" or "Use the timeline tool to correlate events."

**Q: Why does the mock client do nothing?**
A: The CLI mock has an empty plan by default. See `tests/test_harness.py` for examples of creating plans.

**Q: Can I use a different LLM?**
A: Yes! Implement a new client in `llm.py` following the `LLMClient` interface. The `NvidiaClient` is a good template.

**Q: How do I add more tools?**
A: See the "Exercise: Add a New Tool" section in `usefullearn.md` for step-by-step instructions.

**Q: Why do tools return errors as results instead of raising exceptions?**
A: So the LLM can learn from failures and try something different. If an exception crashes the loop, the agent can't recover.

## Summary

You now understand:
- ✅ How to generate sample logs
- ✅ How to run the agent in mock mode
- ✅ How to run the agent with NVIDIA API
- ✅ What the transcript shows you
- ✅ How the agent loop works
- ✅ What brakes do and why they matter
- ✅ How tools are executed
- ✅ What output clamping is
- ✅ How to explore the codebase
- ✅ What makes this a "harness" vs just a loop

**Start experimenting and watch the transcript carefully - that's where you'll learn the most!** 🚀
