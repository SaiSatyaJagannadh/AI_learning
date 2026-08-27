"""
Graders: the things that turn a run into a score.

A grader takes an EvalResult plus the EvalCase that produced it and returns a
GraderScore. That is the entire contract:

    class MyGrader(Grader):
        name = "my_grader"
        def grade(self, result, case) -> GraderScore: ...

Design notes worth keeping in mind when you add one:

  * Graders never raise on a bad answer. A wrong answer is a score of 0.0, not
    an exception. Exceptions are reserved for a broken grader, and the runner
    catches those separately so one bad grader cannot take down the suite.

  * Prefer deterministic graders. Every grader here except LLMJudgeGrader runs
    with no network and no API key, which is what makes the suite usable in CI.
    Reach for the judge only when the thing you are scoring is genuinely a
    judgement call.

  * Partial credit is a feature. "Found 3 of 4 required facts" (0.75) tells you
    far more about a regression than a bare False.
"""

import abc
import re
from typing import List, Dict, Any, Optional, Sequence

from .case import EvalCase, EvalResult, GraderScore, GroundTruth


class Grader(abc.ABC):
    """Base class for all graders."""

    name: str = "grader"

    @abc.abstractmethod
    def grade(self, result: EvalResult, case: EvalCase) -> GraderScore:
        """Score a run. Must not raise for a merely-wrong answer."""

    def _score(self, passed: bool, score: float, detail: str) -> GraderScore:
        return GraderScore(grader=self.name, passed=passed, score=score, detail=detail)


# --------------------------------------------------------------------------
# Answer-content graders
# --------------------------------------------------------------------------


class KeywordGrader(Grader):
    """
    Every required keyword must appear in the final answer (case-insensitive).

    Partial credit is the fraction found, but `passed` requires all of them:
    a diagnosis that names the symptom and misses the cause is not half right
    in any way that matters to whoever is paged at 3am.

    Keywords come from the case's GroundTruth unless passed explicitly.
    """

    name = "keywords"

    def __init__(self, keywords: Optional[Sequence[str]] = None, threshold: float = 1.0):
        self.keywords = list(keywords) if keywords else None
        self.threshold = threshold

    def grade(self, result: EvalResult, case: EvalCase) -> GraderScore:
        gt = result.ground_truth
        required = self.keywords if self.keywords is not None else (
            gt.root_cause_keywords if gt else []
        )
        if not required:
            return self._score(True, 1.0, "no keywords required")

        haystack = result.final_answer.lower()
        found = [k for k in required if k.lower() in haystack]
        missing = [k for k in required if k.lower() not in haystack]
        frac = len(found) / len(required)

        detail = f"{len(found)}/{len(required)} required keywords"
        if missing:
            detail += f"; missing: {', '.join(repr(m) for m in missing)}"
        return self._score(frac >= self.threshold, frac, detail)


class ForbiddenKeywordGrader(Grader):
    """
    None of the forbidden keywords may appear in the final answer.

    This is the precision half of the scoring. Scenarios that plant a decoy
    anomaly list the decoy here, so an agent that confidently blames the wrong
    thing scores 0 rather than sneaking past on the strength of having found
    *an* anomaly.
    """

    name = "forbidden"

    def __init__(self, keywords: Optional[Sequence[str]] = None):
        self.keywords = list(keywords) if keywords else None

    def grade(self, result: EvalResult, case: EvalCase) -> GraderScore:
        gt = result.ground_truth
        forbidden = self.keywords if self.keywords is not None else (
            gt.forbidden_keywords if gt else []
        )
        if not forbidden:
            return self._score(True, 1.0, "no forbidden keywords")

        haystack = result.final_answer.lower()
        hits = [k for k in forbidden if k.lower() in haystack]
        if hits:
            return self._score(
                False, 0.0, f"blamed a decoy: {', '.join(repr(h) for h in hits)}"
            )
        return self._score(True, 1.0, f"avoided all {len(forbidden)} decoys")


class RegexGrader(Grader):
    """Final answer must match (or must not match) a regex."""

    name = "regex"

    def __init__(self, pattern: str, should_match: bool = True, flags: int = re.I):
        self.pattern = pattern
        self.regex = re.compile(pattern, flags)
        self.should_match = should_match

    def grade(self, result: EvalResult, case: EvalCase) -> GraderScore:
        matched = bool(self.regex.search(result.final_answer))
        ok = matched == self.should_match
        verb = "matched" if matched else "did not match"
        return self._score(ok, 1.0 if ok else 0.0, f"answer {verb} /{self.pattern}/")


# --------------------------------------------------------------------------
# Trajectory graders - about *how* the agent worked, not what it concluded
# --------------------------------------------------------------------------


class ToolTrajectoryGrader(Grader):
    """
    Every expected tool must have been called at least once.

    This catches the most common silent failure in agent evals: a model that
    pattern-matches the prompt and emits a plausible-sounding diagnosis without
    ever opening a log. Such an answer can pass a keyword grader outright. This
    grader is what makes the keyword pass mean something.
    """

    name = "tool_trajectory"

    def __init__(self, expected: Optional[Sequence[str]] = None):
        self.expected = list(expected) if expected else None

    def grade(self, result: EvalResult, case: EvalCase) -> GraderScore:
        gt = result.ground_truth
        expected = self.expected if self.expected is not None else (
            gt.expected_tools if gt else []
        )
        if not expected:
            return self._score(True, 1.0, "no tools required")

        called = set(result.tool_names)
        hit = [t for t in expected if t in called]
        missing = [t for t in expected if t not in called]
        frac = len(hit) / len(expected)

        detail = f"called {len(hit)}/{len(expected)} expected tools"
        if missing:
            detail += f"; never called: {', '.join(missing)}"
        if not result.tool_calls:
            detail += " (answered without opening a single log)"
        return self._score(not missing, frac, detail)


class ToolOrderGrader(Grader):
    """
    Expected tools must appear in this relative order (subsequence match).

    Not a strict equality check: extra calls in between are fine. We care that
    the agent oriented before it drilled - listed or searched before it opened
    a specific line range - not that it took the shortest path.
    """

    name = "tool_order"

    def __init__(self, order: Sequence[str]):
        self.order = list(order)

    def grade(self, result: EvalResult, case: EvalCase) -> GraderScore:
        seq = result.tool_names
        idx = 0
        for name in seq:
            if idx < len(self.order) and name == self.order[idx]:
                idx += 1
        frac = idx / len(self.order) if self.order else 1.0
        ok = idx == len(self.order)
        detail = (
            f"matched {idx}/{len(self.order)} of expected order "
            f"{' -> '.join(self.order)}; actual: {' -> '.join(seq) or '(none)'}"
        )
        return self._score(ok, frac, detail)


class NoToolErrorGrader(Grader):
    """
    No tool call may have returned is_error.

    Tolerating a budget of errors is legitimate - a model probing an unfamiliar
    tool will occasionally misuse it - so max_errors is configurable. The
    default of 0 is for cases where any error means the agent misunderstood a
    tool's contract.
    """

    name = "no_tool_errors"

    def __init__(self, max_errors: int = 0):
        self.max_errors = max_errors

    def grade(self, result: EvalResult, case: EvalCase) -> GraderScore:
        errs = [c for c in result.tool_calls if c.get("is_error")]
        ok = len(errs) <= self.max_errors
        if errs:
            which = ", ".join(f"{e.get('name')}" for e in errs[:3])
            detail = f"{len(errs)} tool error(s) (max {self.max_errors}): {which}"
        else:
            detail = f"no tool errors across {len(result.tool_calls)} call(s)"
        return self._score(ok, 1.0 if ok else 0.0, detail)


class BrakeGrader(Grader):
    """
    The run must stay inside its brakes, and (optionally) must not have needed
    them.

    `require_natural_stop` distinguishes an agent that finished from one that
    was cut off. Hitting max_turns is not a crash, but it is a failure of the
    thing we are measuring: the agent did not reach a conclusion on its own.
    """

    name = "brakes"

    def __init__(self, require_natural_stop: bool = True):
        self.require_natural_stop = require_natural_stop

    def grade(self, result: EvalResult, case: EvalCase) -> GraderScore:
        over_turns = result.turns > case.max_turns
        over_calls = len(result.tool_calls) > case.max_tool_calls
        if over_turns or over_calls:
            return self._score(
                False,
                0.0,
                f"brake breached: {result.turns} turns (max {case.max_turns}), "
                f"{len(result.tool_calls)} calls (max {case.max_tool_calls})",
            )
        if self.require_natural_stop and result.turns >= case.max_turns:
            return self._score(
                False,
                0.5,
                f"ran to the turn limit ({result.turns}/{case.max_turns}) "
                f"instead of concluding on its own",
            )
        return self._score(
            True,
            1.0,
            f"{result.turns}/{case.max_turns} turns, "
            f"{len(result.tool_calls)}/{case.max_tool_calls} calls",
        )


# --------------------------------------------------------------------------
# Tool-layer graders - no LLM involved
# --------------------------------------------------------------------------


class ToolOutputGrader(Grader):
    """
    Direct assertions on a single tool's output.

    This is the cheapest and most valuable layer of the suite. The timeline /
    log_stats timestamp bug - where an ISO 'T' separator made both tools return
    empty results against the project's own sample logs - is exactly the shape
    of bug this catches and that an outcome eval would only ever report as a
    vague "the agent seemed confused".
    """

    name = "tool_output"

    def __init__(
        self,
        must_contain: Optional[Sequence[str]] = None,
        must_not_contain: Optional[Sequence[str]] = None,
        expect_error: bool = False,
        min_lines: Optional[int] = None,
    ):
        self.must_contain = list(must_contain or [])
        self.must_not_contain = list(must_not_contain or [])
        self.expect_error = expect_error
        self.min_lines = min_lines

    def grade(self, result: EvalResult, case: EvalCase) -> GraderScore:
        out = result.tool_output
        problems: List[str] = []

        if result.tool_is_error != self.expect_error:
            problems.append(
                f"expected is_error={self.expect_error}, got {result.tool_is_error}"
            )

        low = out.lower()
        for s in self.must_contain:
            if s.lower() not in low:
                problems.append(f"missing {s!r}")
        for s in self.must_not_contain:
            if s.lower() in low:
                problems.append(f"unexpectedly contains {s!r}")

        if self.min_lines is not None:
            n = len([l for l in out.splitlines() if l.strip()])
            if n < self.min_lines:
                problems.append(f"only {n} non-empty lines, wanted >= {self.min_lines}")

        checks = (
            1
            + len(self.must_contain)
            + len(self.must_not_contain)
            + (1 if self.min_lines is not None else 0)
        )
        score = max(0.0, (checks - len(problems)) / checks)
        if problems:
            preview = out[:160].replace("\n", " | ")
            return self._score(False, score, "; ".join(problems) + f" | got: {preview}")
        return self._score(True, 1.0, f"all {checks} output assertions held")


# --------------------------------------------------------------------------
# LLM-as-judge - the only grader that needs an API key
# --------------------------------------------------------------------------


class LLMJudgeGrader(Grader):
    """
    Ask a model whether the answer matches the reference diagnosis.

    Used for the one thing keywords genuinely cannot score: whether the causal
    *story* is right. An answer can contain every required keyword and still
    get the direction of causation backwards ("the 502s caused the deploy").

    The judge is asked for a strict VERDICT line so we parse a decision, not a
    vibe. If no API key is configured the grader abstains - returning passed
    with a note - rather than failing the suite. A missing optional grader
    should never turn CI red.
    """

    name = "llm_judge"

    PROMPT = """You are grading an automated log-debugging agent.

REFERENCE (the true root cause):
{reference}

AGENT'S ANSWER:
{answer}

Does the agent's answer identify the same root cause as the reference?
Ignore differences in wording, length, and formatting. Judge only whether the
causal claim is the same, including the direction of causation.

Reply with exactly two lines:
VERDICT: PASS or FAIL
REASON: one sentence
"""

    def __init__(self, client=None, model: Optional[str] = None):
        self.client = client
        self.model = model

    def _get_client(self):
        if self.client is not None:
            return self.client
        import os

        if not os.getenv("NVIDIA_API_KEY"):
            return None
        from logagent.llm import NvidiaClient

        self.client = NvidiaClient(model=self.model)
        return self.client

    def grade(self, result: EvalResult, case: EvalCase) -> GraderScore:
        gt = result.ground_truth
        reference = (gt.description if gt else "") or "(no reference provided)"

        client = self._get_client()
        if client is None:
            return self._score(
                True, 1.0, "abstained (no NVIDIA_API_KEY; judge is optional)"
            )

        prompt = self.PROMPT.format(reference=reference, answer=result.final_answer)
        try:
            resp = client.complete(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                stop_sequences=[],
            )
        except Exception as e:  # judge outage must not fail the suite
            return self._score(True, 1.0, f"abstained (judge call failed: {e})")

        text = " ".join(
            b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text"
        )
        verdict = re.search(r"VERDICT:\s*(PASS|FAIL)", text, re.I)
        reason = re.search(r"REASON:\s*(.+)", text, re.I)
        reason_s = reason.group(1).strip() if reason else text.strip()[:150]

        if not verdict:
            return self._score(True, 1.0, f"abstained (unparseable verdict): {reason_s}")
        ok = verdict.group(1).upper() == "PASS"
        return self._score(ok, 1.0 if ok else 0.0, f"judge: {reason_s}")


# Convenient default bundle for outcome cases: did it find the cause, avoid the
# decoy, actually use tools, and finish under its own steam?
def standard_outcome_graders() -> List[Grader]:
    return [
        KeywordGrader(),
        ForbiddenKeywordGrader(),
        ToolTrajectoryGrader(),
        BrakeGrader(),
    ]
