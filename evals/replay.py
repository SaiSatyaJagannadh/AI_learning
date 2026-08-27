"""
ScriptedClient: a deterministic stand-in for the LLM.

Why this exists
---------------
An eval suite that needs an API key is an eval suite nobody runs. Network calls
are slow, cost money, and - worst of all for a regression suite - are
nondeterministic, so a red run tells you nothing until you have run it three
more times.

ScriptedClient replaces the model with a fixed script. That turns "does the
agent behave correctly" into a deterministic, offline, millisecond-fast
question. What we give up is real reasoning; what we keep is every other moving
part - the harness loop, the registry, the real tools reading real log files,
the graders. When the suite goes red, the model is the one thing that cannot
be at fault, which is precisely what you want from a regression gate.

The live model still gets evaluated: run the same outcome cases with
`--nvidia` to score actual reasoning. Scripted runs are the gate; live runs are
the measurement.

Script format
-------------
A script is a list of turns. Each turn is one of:

    {"tools": [{"name": "search_logs", "input": {...}}, ...]}   -> stop_reason "tool_use"
    {"text": "final answer"}                                     -> stop_reason "stop"
    {"pause": True}                                              -> stop_reason "pause_turn"
    {"text": "...", "tools": [...]}                              -> text + tool_use together

A turn's `input` may be a callable taking the previous turn's tool-result text
and returning the dict. That is how a script stays honest about data flow: the
second call can depend on what the first actually returned, so a tool that
starts returning nothing breaks the script instead of being papered over.

When the script runs out, the client returns a final stop turn, so a script
that is shorter than the harness's turn budget still terminates cleanly.
"""

from typing import List, Dict, Any, Optional, Callable, Union

from logagent.llm import LLMClient


class ScriptedClient(LLMClient):
    """Replays a fixed list of turns. See module docstring for the format."""

    def __init__(
        self,
        script: List[Dict[str, Any]],
        final_text: str = "Investigation complete.",
    ):
        self.script = list(script or [])
        self.final_text = final_text
        self.turn = 0

        # Text of the most recent tool result, fed to callable inputs so a
        # later turn can react to what an earlier tool actually produced.
        self.last_tool_text: str = ""

        # Every request the harness made, for assertions about what the
        # harness sent (message shape, verbatim content, brakes).
        self.calls: List[Dict[str, Any]] = []

    # -- harness feeds results back through this -------------------------

    def set_last_tool_result(self, result: Dict[str, Any]) -> None:
        """Record a tool result so callable inputs can react to it."""
        parts = []
        for block in (result or {}).get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        self.last_tool_text = "\n".join(parts)

    # -- LLMClient ------------------------------------------------------

    def complete(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: int,
        stop_sequences: List[str],
    ) -> Dict[str, Any]:
        self.calls.append(
            {
                "messages": messages,
                "max_tokens": max_tokens,
                "n_messages": len(messages),
            }
        )

        if self.turn >= len(self.script):
            return self._stop(self.final_text, messages)

        step = self.script[self.turn]
        self.turn += 1

        if step.get("pause"):
            return {
                "stop_reason": "pause_turn",
                "content": [{"type": "text", "text": step.get("text", "Pausing.")}],
                "usage": {"input_tokens": 0, "output_tokens": 5},
            }

        tools = step.get("tools")
        if not tools:
            return self._stop(step.get("text", self.final_text), messages)

        content: List[Dict[str, Any]] = []
        if step.get("text"):
            content.append({"type": "text", "text": step["text"]})

        for i, spec in enumerate(tools):
            raw_input: Union[Dict[str, Any], Callable] = spec.get("input", {})
            if callable(raw_input):
                try:
                    tool_input = raw_input(self.last_tool_text)
                except Exception as e:
                    # Surface it as a tool arg the real tool will reject, so
                    # the failure shows up as a red eval rather than a silent
                    # fallback to a default that happens to work.
                    tool_input = {"__script_error__": str(e)}
            else:
                tool_input = dict(raw_input)

            content.append(
                {
                    "type": "tool_use",
                    "id": f"toolu_{self.turn}_{i}",
                    "name": spec.get("name"),
                    "input": tool_input,
                }
            )

        return {
            "stop_reason": "tool_use",
            "content": content,
            "usage": {"input_tokens": self._rough_tokens(messages), "output_tokens": 25},
        }

    # -- helpers --------------------------------------------------------

    def _stop(self, text: str, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "stop_reason": "stop",
            "content": [{"type": "text", "text": text}],
            "usage": {
                "input_tokens": self._rough_tokens(messages),
                "output_tokens": max(1, len(text) // 4),
            },
        }

    @staticmethod
    def _rough_tokens(messages: List[Dict[str, Any]]) -> int:
        return sum(len(str(m.get("content", ""))) // 4 for m in messages)


class AnswerOnlyClient(ScriptedClient):
    """
    Returns a canned answer immediately, calling no tools at all.

    This is the negative control. Point it at an outcome case whose answer text
    is word-perfect and the keyword grader will happily pass it - which is the
    demonstration that keyword graders alone are not enough, and why every
    outcome case also carries a ToolTrajectoryGrader. The suite includes one
    such case, asserted to FAIL, so that guarantee is itself tested.
    """

    def __init__(self, answer: str):
        super().__init__(script=[], final_text=answer)
