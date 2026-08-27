"""
The execution half of the eval suite: turn an EvalCase into a graded EvalResult.

Why this module exists
----------------------
case.py defines *what* a question is and graders.py defines *how* an answer is
scored, but neither one touches the agent. Something has to stand between them:
build a real registry over a real scenario's log files, drive the real harness,
record what actually happened, and hand that record to the graders. That is all
this file does, and keeping it separate is what lets the graders be unit tested
against hand-built EvalResults with no agent in sight.

Two design decisions carry most of the weight here.

**The registry is instrumented, not the harness.** We need an ordered record of
every tool call the agent made, and the obvious place to get it is the harness -
but editing the harness to serve the eval suite would mean the suite no longer
measures the harness people actually ship. Instead we wrap
`registry.execute_tool`, exactly the way tests/test_harness.py already does. The
wrapper is also where the scripted client gets fed the previous tool result, so
callable script inputs can react to real tool output. One seam, two jobs, zero
changes to production code.

**Everything is contained.** A grader that raises becomes a failed GraderScore
rather than an exception that kills the suite; a run that blows up sets
`result.error` and still gets graded, so a broken harness stays visually
distinct from a wrong answer. An eval suite whose own failures look like
scenario failures is worse than no suite at all.

Scenario ground truth is looked up lazily (see `_lookup_ground_truth`) so this
module can be imported without evals/scenarios.py existing yet, and so the two
files never form an import cycle.
"""

import time
import traceback
from typing import Any, Callable, Dict, List, Optional

from logagent.harness import AgentHarness
from logagent.llm import LLMClient
from logagent.logtools import LogTools
from logagent.tools import ToolRegistry
from logagent.transcript import Transcript

from .case import EvalCase, EvalResult, GraderScore, GroundTruth, SuiteReport


# --------------------------------------------------------------------------
# Registry construction
# --------------------------------------------------------------------------


def build_registry(log_tools: LogTools) -> ToolRegistry:
    """
    Register the five log tools exactly as cli.py does.

    The names, descriptions and JSON Schemas are copied verbatim from the CLI on
    purpose. A model's behaviour is a function of its tool descriptions as much
    as of its prompt, so an eval that registered a "cleaned up" schema would be
    measuring a harness nobody runs. If cli.py's wording changes, this must
    change with it - that coupling is the point, not an accident.
    """
    registry = ToolRegistry()

    registry.register_tool(
        name="list_logs",
        description="List log files in the log root with their sizes. Use this to discover what logs are available.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match log files (relative to log root), default '*'",
                }
            },
            "additionalProperties": False,
        },
        function=log_tools.list_logs,
        dangerous=False,
    )

    registry.register_tool(
        name="search_logs",
        description="Search log files with a regex pattern. Returns matching lines with context. Use this to find specific error messages or patterns.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regular expression pattern to search for",
                },
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of log files to search (relative to log root). If not provided, all logs are searched.",
                },
                "context_lines": {
                    "type": "integer",
                    "description": "Number of context lines to include around each match (default: 0)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of matches to return (default: 100)",
                },
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
        function=log_tools.search_logs,
        dangerous=False,
    )

    registry.register_tool(
        name="read_log",
        description="Read a log file with pagination by line range. Use this to examine specific parts of a log file.",
        parameters={
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Log file path relative to log root",
                },
                "start_line": {
                    "type": "integer",
                    "description": "1-indexed start line (default: 1)",
                },
                "end_line": {
                    "type": "integer",
                    "description": "1-indexed end line (inclusive, default: start_line + 99)",
                },
            },
            "required": ["file"],
            "additionalProperties": False,
        },
        function=log_tools.read_log,
        dangerous=False,
    )

    registry.register_tool(
        name="log_stats",
        description="Compute statistics on log files: severity counts (ERROR, WARN, INFO, DEBUG) and errors-over-time histogram (hourly buckets).",
        parameters={
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of log files to analyze (relative to log root). If not provided, all logs are used.",
                },
            },
            "additionalProperties": False,
        },
        function=log_tools.log_stats,
        dangerous=False,
    )

    registry.register_tool(
        name="timeline",
        description="Merge several log files into one chronological view around a timestamp. Use this to correlate events across multiple logs.",
        parameters={
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of log files to merge (relative to log root)",
                },
                "around": {
                    "type": "string",
                    "description": "Center timestamp in format 'YYYY-MM-DD HH:MM:SS'",
                },
                "window": {
                    "type": "string",
                    "description": "Time window around the timestamp (format HH:MM:SS or seconds), default '00:05:00' (5 minutes)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to return (default: 100)",
                },
            },
            "required": ["files", "around"],
            "additionalProperties": False,
        },
        function=log_tools.timeline,
        dangerous=False,
    )

    return registry


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _text_of(tool_result: Optional[Dict[str, Any]]) -> str:
    """Flatten a tool result's text blocks into one string (non-text ignored)."""
    parts: List[str] = []
    for block in (tool_result or {}).get("content", []) or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


def _short_traceback(exc: BaseException) -> str:
    """
    One line: exception type, message, and the frame it came from.

    A full traceback in a report drowns out the twenty results around it. The
    innermost frame is almost always enough to tell "the scenario fixture is
    missing" apart from "the harness loop broke", and the exception is still
    re-raisable from a debugger if more is needed.
    """
    frames = traceback.extract_tb(exc.__traceback__)
    where = ""
    if frames:
        last = frames[-1]
        filename = last.filename.rsplit("/", 1)[-1]
        where = f" at {filename}:{last.lineno} in {last.name}()"
    return f"{type(exc).__name__}: {exc}{where}"


def _lookup_ground_truth(scenario: str) -> Optional[GroundTruth]:
    """
    Fetch a scenario's answer key, tolerating an absent scenarios module.

    Imported inside the function for two reasons: evals/scenarios.py imports the
    eval types (and may want the registry builder above), so a module-level
    import risks a cycle; and the runner stays usable - and testable - before
    the scenario fixtures exist. A missing answer key is not fatal: graders that
    read ground truth fall back to "nothing required", so the run still reports
    rather than crashing.
    """
    try:
        from . import scenarios  # noqa: WPS433 - deliberate lazy import
    except Exception:
        return None

    # Accept whichever shape scenarios.py settled on. Cheaper than coupling the
    # runner to one accessor name that may be refactored later.
    getter = getattr(scenarios, "get_ground_truth", None)
    if callable(getter):
        try:
            return getter(scenario)
        except Exception:
            return None

    for attr in ("GROUND_TRUTH", "GROUND_TRUTHS", "SCENARIOS"):
        table = getattr(scenarios, attr, None)
        if isinstance(table, dict) and scenario in table:
            entry = table[scenario]
            if isinstance(entry, GroundTruth):
                return entry
            # A scenario object that carries its own ground truth.
            gt = getattr(entry, "ground_truth", None)
            if isinstance(gt, GroundTruth):
                return gt
    return None


def _client_label(client: Optional[LLMClient]) -> str:
    """Name the model under test, for the report header."""
    model = getattr(client, "model", None)
    if isinstance(model, str) and model:
        return model
    return "scripted"


# --------------------------------------------------------------------------
# The runner
# --------------------------------------------------------------------------


class EvalRunner:
    """
    Runs cases against real tools over a scenario's real log files.

    Both factories are injected rather than hardcoded so the same runner drives
    the scripted regression gate and a live-model measurement run: swap the
    client factory and nothing else changes. The scenario directory factory is
    injected for the same reason - fixtures may be generated into a tmp_path
    under pytest and into a persistent directory from the CLI.
    """

    def __init__(
        self,
        client_factory: Callable[[EvalCase], LLMClient],
        scenario_dir_factory: Callable[[str], str],
        verbose: bool = False,
        output_char_budget: int = 500,
    ):
        self.client_factory = client_factory
        self.scenario_dir_factory = scenario_dir_factory
        self.verbose = verbose
        # Tools clamp their own output to this budget. Keeping it small is part
        # of what the suite tests: an agent that only ever sees clamped output
        # has to compose tools instead of slurping whole files.
        self.output_char_budget = output_char_budget
        self._last_client_label = "scripted"

    # -- instrumentation ------------------------------------------------

    def _instrument(
        self,
        registry: ToolRegistry,
        result: EvalResult,
        client: Optional[LLMClient],
    ) -> None:
        """
        Wrap execute_tool so every call is recorded and echoed to the client.

        Same seam tests/test_harness.py uses. It does double duty: the record it
        appends is what every trajectory grader reads, and the
        set_last_tool_result callback is what lets a ScriptedClient's callable
        inputs depend on what the previous tool really returned - so a tool that
        starts returning nothing breaks the script loudly instead of silently
        replaying stale arguments.
        """
        original_execute_tool = registry.execute_tool
        feedback = getattr(client, "set_last_tool_result", None)

        def execute_and_record(name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
            tool_result = original_execute_tool(name, tool_input)
            result.tool_calls.append(
                {
                    "name": name,
                    "input": tool_input,
                    "is_error": bool((tool_result or {}).get("is_error", False)),
                    "output": _text_of(tool_result),
                }
            )
            if callable(feedback):
                # Never let a client-side bookkeeping bug look like a tool
                # failure - the tool result is what the harness needs back.
                try:
                    feedback(tool_result)
                except Exception:
                    pass
            return tool_result

        registry.execute_tool = execute_and_record  # type: ignore[method-assign]

    # -- grading --------------------------------------------------------

    def _grade(self, result: EvalResult, case: EvalCase) -> List[GraderScore]:
        """
        Run every grader, converting a raising grader into a failing score.

        A grader that throws is a bug in the grader, not evidence about the
        agent - but silently dropping it would quietly weaken the case it was
        supposed to strengthen. Recording it as a visible failure means a broken
        grader turns the suite red at the exact case it belongs to.
        """
        scores: List[GraderScore] = []
        for grader in case.graders:
            name = getattr(grader, "name", type(grader).__name__)
            try:
                scores.append(grader.grade(result, case))
            except Exception as e:
                scores.append(
                    GraderScore(
                        grader=name,
                        passed=False,
                        score=0.0,
                        detail=f"grader raised: {_short_traceback(e)}",
                    )
                )
        return scores

    # -- the two entry points -------------------------------------------

    def run_case(self, case: EvalCase) -> EvalResult:
        """Execute one case and return it fully graded."""
        result = EvalResult(case_id=case.id, layer=case.layer)

        # Attached before the run so it survives a crash, and before grading
        # because every keyword/trajectory grader reads its answer key from
        # result.ground_truth rather than from the case.
        result.ground_truth = _lookup_ground_truth(case.scenario)

        if self.verbose and case.rationale:
            print(f"[{case.id}] {case.rationale}")

        started = time.perf_counter()
        try:
            log_root = self.scenario_dir_factory(case.scenario)
            log_tools = LogTools(
                log_root=log_root, output_char_budget=self.output_char_budget
            )
            registry = build_registry(log_tools)

            if case.layer == "tool":
                # No LLM at all: the case *is* the tool call. Instrumenting the
                # registry anyway keeps tool_calls populated, so tool-layer
                # failures read the same way as every other layer.
                self._instrument(registry, result, client=None)
                tool_result = registry.execute_tool(
                    case.tool_name or "", dict(case.tool_input)
                )
                result.tool_output = _text_of(tool_result)
                result.tool_is_error = bool((tool_result or {}).get("is_error", False))
                result.turns = 0
            else:
                client = self.client_factory(case)
                self._last_client_label = _client_label(client)
                self._instrument(registry, result, client=client)

                harness = AgentHarness(
                    transcript=Transcript(enabled=self.verbose),
                    tool_registry=registry,
                    llm_client=client,
                    max_turns=case.max_turns,
                    max_tool_calls=case.max_tool_calls,
                    max_output_tokens=case.max_output_tokens,
                )
                result.final_answer = harness.run(case.prompt)
                # The harness owns the loop counter; reading it back afterwards
                # is how BrakeGrader tells "concluded" from "was cut off".
                result.turns = harness.turn_count
        except Exception as e:
            result.error = _short_traceback(e)
        finally:
            result.duration_s = time.perf_counter() - started

        # Graded even on error: EvalResult.passed already forces False when
        # error is set, and the grader details explain what the run did manage
        # to do before it fell over.
        result.scores = self._grade(result, case)
        return result

    def run_suite(self, cases: List[EvalCase]) -> SuiteReport:
        """Run every case in order and aggregate into a SuiteReport."""
        started = time.perf_counter()
        results = [self.run_case(case) for case in cases]
        return SuiteReport(
            results=results,
            duration_s=time.perf_counter() - started,
            client_label=self._last_client_label,
        )
