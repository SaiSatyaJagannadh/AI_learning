"""
Transcript for tracing the agent's execution.

Prints every turn, every tool call with its arguments, and a preview of every
result. This helps debug the agent loop.
"""

import sys
import json
from typing import List, Dict, Any
from datetime import datetime


class Transcript:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.session_id: Optional[str] = None
        self.turn_count = 0

    def start(self, session_id: str = "default"):
        """Start a new session."""
        if not self.enabled:
            return
        self.session_id = session_id
        self.turn_count = 0
        print(f"\n{'='*60}")
        print(f"Session started: {self.session_id} at {datetime.now()}")
        print(f"{'='*60}\n")

    def log_turn_start(self, turn_num: int):
        """Log the start of a turn."""
        if not self.enabled:
            return
        print(f"\n--- Turn {turn_num} ---")

    def log_assistant_turn(self, content_blocks: List[Dict[str, Any]], stop_reason: str):
        """Log the assistant's response."""
        if not self.enabled:
            return
        print("Assistant:")
        for block in content_blocks:
            if block.get("type") == "text":
                text = block.get("text", "")
                # Truncate long text for preview
                if len(text) > 200:
                    print(f"  {text[:200]}...")
                else:
                    print(f"  {text}")
            elif block.get("type") == "tool_use":
                print(f"  -> Tool use: {block.get('name')}")
                print(f"     ID: {block.get('id')}")
                print(f"     Input: {json.dumps(block.get('input', {}), indent=2)}")
        print(f"  Stop reason: {stop_reason}")

    def log_tool_call(
        self, tool_name: str, tool_input: Dict[str, Any], result: Any, is_error: bool
    ):
        """Log a tool call and its result."""
        if not self.enabled:
            return
        print(f"  Tool call: {tool_name}")
        print(f"    Input: {json.dumps(tool_input, indent=2)}")
        # Preview result
        if isinstance(result, dict) and "content" in result:
            content = result["content"]
            if content and isinstance(content, list):
                for block in content:
                    if block.get("type") == "text":
                        text = block.get("text", "")
                        if len(text) > 200:
                            print(f"    Result: {text[:200]}...")
                        else:
                            print(f"    Result: {text}")
                        break
        elif isinstance(result, str):
            if len(result) > 200:
                print(f"    Result: {result[:200]}...")
            else:
                print(f"    Result: {result}")
        else:
            print(f"    Result: {result}")
        if is_error:
            print("    !! ERROR !!")

    def log_tool_results(self, tool_results: List[Dict[str, Any]]):
        """Log multiple tool results (after a turn with several tool calls)."""
        if not self.enabled:
            return
        print("  Tool results:")
        for res in tool_results:
            print(f"    Tool use ID: {res.get('tool_use_id')}")
            if res.get("is_error"):
                print("      !! ERROR !!")
            content = res.get("content", [])
            if content and isinstance(content, list):
                for block in content:
                    if block.get("type") == "text":
                        text = block.get("text", "")
                        if len(text) > 200:
                            print(f"      {text[:200]}...")
                        else:
                            print(f"      {text}")
                        break

    def log_tool_error(self, tool_name: str, tool_input: Dict[str, Any], error: str):
        """Log a tool execution error."""
        if not self.enabled:
            return
        print(f"  Tool execution failed: {tool_name}")
        print(f"    Input: {json.dumps(tool_input, indent=2)}")
        print(f"    Error: {error}")

    def log_pause_turn(self):
        """Log that the agent paused."""
        if not self.enabled:
            return
        print(f"\n*** Agent paused turn ***\n")

    def log_final_stop(self, stop_reason: str):
        """Log the final stop reason."""
        if not self.enabled:
            return
        print(f"\n=== Session finished ===")
        print(f"Stop reason: {stop_reason}")
        print(f"{'='*60}\n")