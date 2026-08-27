"""
Core data types for the eval suite.

An eval is a question we can score automatically. This module defines the
vocabulary every other eval module speaks:

  GroundTruth  - what the right answer looks like for a scenario
  EvalCase     - one question put to the agent, plus the graders that score it
  GraderScore  - one grader's verdict on one run
  EvalResult   - everything observable about a single agent run
  SuiteReport  - aggregate over many results

Nothing here imports the harness. Keeping the types dependency-free means the
graders can be unit tested against hand-built EvalResults, with no agent, no
tools, and no LLM in the loop.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # avoid a circular import at runtime
    from .graders import Grader


# Which layer of the pyramid a case belongs to. The layers differ in what they
# hold fixed, not in how they are scored:
#   tool       - no LLM at all; call a tool directly and check its output
#   trajectory - scripted LLM; check how the harness drove the loop
#   outcome    - scripted or live LLM; check the final diagnosis
LAYERS = ("tool", "trajectory", "outcome")


@dataclass
class GroundTruth:
    """
    What a correct investigation of a scenario looks like.

    This is the answer key. It is deliberately expressed as keywords and tool
    names rather than an exact expected string: there are many correct ways to
    phrase "the deploy raised DB_POOL_SIZE past what the database allows", and
    an eval that demands one phrasing measures wording, not understanding.
    """

    # Every one of these must appear in the final answer (case-insensitive).
    # Keep the list short and causal - these are the load-bearing facts.
    root_cause_keywords: List[str] = field(default_factory=list)

    # None of these may appear. This is how we score precision: a scenario with
    # a decoy anomaly lists the decoy here, so an agent that blames the decoy
    # fails even though it "found something".
    forbidden_keywords: List[str] = field(default_factory=list)

    # Tools the agent must call at least once to have actually investigated
    # rather than guessed from the prompt.
    expected_tools: List[str] = field(default_factory=list)

    # Where the root cause physically lives, for tool-layer assertions.
    culprit_file: str = ""
    culprit_timestamp: str = ""

    # Free-text description of the true root cause. Used as the reference
    # answer for the LLM judge, and printed in failure output so a human
    # reading a red test knows what was supposed to happen.
    description: str = ""


@dataclass
class EvalCase:
    """One scored question put to the agent."""

    id: str
    layer: str
    prompt: str

    # Name of the scenario whose fixture logs this case runs against.
    scenario: str

    # How this case is scored. Multiple graders per case is the norm: one for
    # "did it find the cause", one for "did it actually use the tools".
    graders: List["Grader"] = field(default_factory=list)

    # Per-case brakes. Deliberately tighter than the harness defaults - an eval
    # that lets the agent wander for 10 turns is measuring patience, not skill.
    max_turns: int = 8
    max_tool_calls: int = 12
    max_output_tokens: int = 4000

    # For layer == "tool": call this tool directly with these args instead of
    # running the agent loop at all.
    tool_name: Optional[str] = None
    tool_input: Dict[str, Any] = field(default_factory=dict)

    # For layer in ("trajectory", "outcome") under a scripted client: the
    # turn-by-turn script the fake model replays. See evals/replay.py.
    script: Optional[List[Dict[str, Any]]] = None

    # Free-text note explaining what this case is really probing, printed in
    # verbose runs. A case nobody can explain is a case nobody will maintain.
    rationale: str = ""

    def __post_init__(self):
        if self.layer not in LAYERS:
            raise ValueError(
                f"EvalCase {self.id!r}: layer must be one of {LAYERS}, got {self.layer!r}"
            )
        if self.layer == "tool" and not self.tool_name:
            raise ValueError(
                f"EvalCase {self.id!r}: tool-layer cases must set tool_name"
            )


@dataclass
class GraderScore:
    """One grader's verdict on one run."""

    grader: str
    passed: bool
    score: float  # 0.0 .. 1.0, for partial credit
    detail: str  # human-readable "why", shown on failure

    def __post_init__(self):
        # Clamp rather than raise: a grader with an off-by-one in its own
        # arithmetic should show up as a bad score, not crash the whole suite.
        self.score = max(0.0, min(1.0, float(self.score)))


@dataclass
class EvalResult:
    """Everything observable about a single run, before grading."""

    case_id: str
    layer: str

    # The agent's final text answer (empty for tool-layer cases).
    final_answer: str = ""

    # Ordered record of every tool the agent invoked:
    #   {"name": str, "input": dict, "is_error": bool, "output": str}
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)

    # Raw output of a directly-invoked tool (tool-layer cases only).
    tool_output: str = ""
    tool_is_error: bool = False

    turns: int = 0
    duration_s: float = 0.0

    # Set when the run itself blew up (not when the agent merely answered
    # badly). An errored run fails every grader, but we keep the traceback so
    # a broken harness is distinguishable from a wrong answer.
    error: Optional[str] = None

    # Filled in by the runner after grading.
    scores: List[GraderScore] = field(default_factory=list)

    # The ground truth this run was scored against, for reporting.
    ground_truth: Optional[GroundTruth] = None

    @property
    def tool_names(self) -> List[str]:
        """Tool call names in invocation order (with repeats)."""
        return [c.get("name", "") for c in self.tool_calls]

    @property
    def passed(self) -> bool:
        """A run passes only if every grader passed and nothing crashed."""
        if self.error is not None:
            return False
        if not self.scores:
            return False
        return all(s.passed for s in self.scores)

    @property
    def score(self) -> float:
        """Mean grader score. 0.0 for a crashed or ungraded run."""
        if self.error is not None or not self.scores:
            return 0.0
        return sum(s.score for s in self.scores) / len(self.scores)


@dataclass
class SuiteReport:
    """Aggregate over a whole run of the suite."""

    results: List[EvalResult] = field(default_factory=list)
    duration_s: float = 0.0
    # Label for the model/client this run used, e.g. "scripted" or a model id.
    client_label: str = "scripted"

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def errored(self) -> int:
        return sum(1 for r in self.results if r.error is not None)

    @property
    def pass_rate(self) -> float:
        return (self.passed / self.total) if self.total else 0.0

    @property
    def mean_score(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.score for r in self.results) / len(self.results)

    def by_layer(self) -> Dict[str, List[EvalResult]]:
        """Group results by layer, preserving LAYERS order."""
        out: Dict[str, List[EvalResult]] = {layer: [] for layer in LAYERS}
        for r in self.results:
            out.setdefault(r.layer, []).append(r)
        return {k: v for k, v in out.items() if v}
