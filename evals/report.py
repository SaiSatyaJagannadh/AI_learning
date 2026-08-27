"""
Rendering a SuiteReport for the three audiences that read eval output.

An eval suite is only as useful as its failure message. A run that says
"7/12 passed" and stops has moved the work, not done it: someone still has to
re-run with a debug flag, guess which grader complained, and reconstruct what
the agent actually did. So the rule this module follows is that **a red run
must be diagnosable from the terminal alone**. Every failing case prints the
detail string of every grader that failed it, right underneath its row.

Three renderers, because there are three readers:

  render_console  - a human watching a run. Aligned ASCII, grouped by layer,
                    failures expanded inline.
  render_markdown - a human reading a PR or CI artifact later.
  render_json     - a machine (or a diff) tracking scores across commits.

Deliberate constraints:

  * ASCII only. No box-drawing, no emoji, no ANSI color. This text gets piped
    into files, GitHub Actions logs, and terminals with no TTY; a color code
    that survives into a log file is noise, and a unicode glyph that lands in
    a cp1252 console is a crash.
  * Column widths are computed from the data, never hardcoded, so a long case
    id widens the column instead of wrapping the table into unreadability.
  * Every aggregate guards against an empty suite. A report with zero results
    is a legitimate state (a filter matched nothing) and must render, not
    divide by zero.
"""

import json
import os
from typing import Dict, List, Optional, Sequence, Tuple

from .case import EvalResult, SuiteReport


# Final answers are clamped in the JSON report. Full answers can run to
# thousands of characters under a live model, which turns every commit into an
# unreadable diff. We clamp and say so, the same contract the log tools use.
ANSWER_CLAMP = 800

# Console failure details are clamped too - a grader that dumps a 2KB tool
# preview would push the rest of the table off screen.
DETAIL_CLAMP = 300


# --------------------------------------------------------------------------
# Small shared helpers
# --------------------------------------------------------------------------


def _status(result: EvalResult) -> str:
    """
    PASS / FAIL / ERROR.

    ERROR is broken out from FAIL even though both count as "not passed":
    a wrong answer is a model problem, a traceback is our problem, and
    conflating them wastes the first ten minutes of every triage.
    """
    if result.error is not None:
        return "ERROR"
    return "PASS" if result.passed else "FAIL"


def _clamp(text: str, limit: int) -> str:
    """Clamp with an explicit note about what was held back."""
    text = (text or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}... [{len(text) - limit} more chars]"


def _tool_chain(result: EvalResult) -> str:
    """Tool calls as 'search_logs -> read_log -> timeline'."""
    names = [n for n in result.tool_names if n]
    return " -> ".join(names) if names else "(no tool calls)"


def _failing_scores(result: EvalResult) -> List[Tuple[str, str]]:
    """(grader_name, detail) for every grader that failed this run."""
    return [(s.grader, s.detail) for s in result.scores if not s.passed]


def _diagnosis_lines(result: EvalResult, indent: str) -> List[str]:
    """
    The 'why did this go red' block printed under a failing row.

    Order matters: the crash (if any) first, then each failing grader, then
    the reference answer. Someone scanning a CI log reads top-down and should
    hit the most actionable thing first.
    """
    lines: List[str] = []

    if result.error is not None:
        for i, line in enumerate((result.error or "").splitlines() or [""]):
            # Only the first line is load-bearing for triage; the rest is
            # usually a traceback tail, so indent it as a continuation.
            prefix = "error: " if i == 0 else "       "
            lines.append(f"{indent}{prefix}{line}")

    for name, detail in _failing_scores(result):
        lines.append(f"{indent}{name}: {_clamp(detail, DETAIL_CLAMP)}")

    # A run with no scores and no error graded as nothing - that is a runner
    # bug, and silence about it is how it survives to the next release.
    if not result.scores and result.error is None:
        lines.append(f"{indent}(no graders ran for this case)")

    gt = result.ground_truth
    if gt is not None and gt.description:
        lines.append(f"{indent}expected: {_clamp(gt.description, DETAIL_CLAMP)}")

    return lines


# --------------------------------------------------------------------------
# ASCII table
# --------------------------------------------------------------------------


def _render_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    aligns: Sequence[str],
    indent: str = "",
) -> List[str]:
    """
    Render a fixed-width ASCII table, sizing each column to its widest cell.

    `aligns` is one of "l"/"r" per column. Numbers right-align so scores line
    up on the decimal point and a regression is visible by eye; text
    left-aligns.
    """
    ncols = len(headers)
    widths = [len(h) for h in headers]
    for row in rows:
        for i in range(ncols):
            cell = row[i] if i < len(row) else ""
            widths[i] = max(widths[i], len(cell))

    def fmt_row(cells: Sequence[str]) -> str:
        out = []
        for i in range(ncols):
            cell = cells[i] if i < len(cells) else ""
            out.append(cell.rjust(widths[i]) if aligns[i] == "r" else cell.ljust(widths[i]))
        # rstrip so trailing padding does not show up as whitespace diffs
        return (indent + "  ".join(out)).rstrip()

    lines = [fmt_row(headers), fmt_row(["-" * w for w in widths])]
    lines.extend(fmt_row(r) for r in rows)
    return lines


def _row_for(result: EvalResult) -> List[str]:
    return [
        result.case_id,
        _status(result),
        f"{result.score:.2f}",
        str(result.turns),
        str(len(result.tool_calls)),
        f"{result.duration_s:.2f}s",
    ]


_HEADERS = ("CASE", "STATUS", "SCORE", "TURNS", "CALLS", "TIME")
_ALIGNS = ("l", "l", "r", "r", "r", "r")


# --------------------------------------------------------------------------
# Console
# --------------------------------------------------------------------------


def render_console(report: SuiteReport, verbose: bool = False) -> str:
    """
    Human-readable run summary as a single string.

    Returns rather than prints so the same text can be asserted on in tests,
    written to a file, and echoed to stdout by the caller.
    """
    lines: List[str] = []
    rule = "=" * 72

    lines.append(rule)
    lines.append(f"EVAL SUITE  client={report.client_label}")
    lines.append(rule)

    if not report.results:
        # An empty suite is a real outcome (a filter matched nothing), and it
        # must not look like a pass. Say so plainly and skip the arithmetic.
        lines.append("")
        lines.append("No cases ran. (Did a filter match nothing?)")
        lines.append(rule)
        return "\n".join(lines)

    for layer, results in report.by_layer().items():
        n_pass = sum(1 for r in results if r.passed)
        lines.append("")
        lines.append(f"LAYER: {layer}  ({n_pass}/{len(results)} passed)")

        table = _render_table(_HEADERS, [_row_for(r) for r in results], _ALIGNS, indent="  ")
        # Header + separator, then one row per case with its diagnosis block
        # interleaved. Interleaving beats a failures-appendix: the detail sits
        # next to the row it explains, so no scrolling back and forth.
        lines.append(table[0])
        lines.append(table[1])
        for result, row_line in zip(results, table[2:]):
            lines.append(row_line)
            if verbose:
                lines.append(f"      tools: {_tool_chain(result)}")
                for s in result.scores:
                    mark = "pass" if s.passed else "FAIL"
                    lines.append(f"      [{mark}] {s.grader}: {_clamp(s.detail, DETAIL_CLAMP)}")
                if result.error is not None:
                    lines.append(f"      error: {_clamp(result.error, DETAIL_CLAMP)}")
            elif not result.passed:
                lines.extend(_diagnosis_lines(result, indent="      "))

    lines.append("")
    lines.append("-" * 72)
    summary = (
        f"SUMMARY  {report.passed}/{report.total} passed "
        f"({report.pass_rate * 100:.1f}%)  "
        f"mean score {report.mean_score:.2f}  "
        f"wall {report.duration_s:.2f}s"
    )
    if report.errored:
        summary += f"  [{report.errored} errored]"
    lines.append(summary)

    failed_ids = [r.case_id for r in report.results if not r.passed]
    if failed_ids:
        # Repeat the failing ids at the bottom: CI logs get truncated from the
        # top, and this is the line someone copies into a re-run command.
        lines.append(f"FAILED   {', '.join(failed_ids)}")
    lines.append("-" * 72)

    return "\n".join(lines)


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------


def _md_escape(text: str) -> str:
    """Pipes would split a table cell in two; backticks would unbalance code spans."""
    return (text or "").replace("|", "\\|").replace("`", "'").replace("\n", " ")


def render_markdown(report: SuiteReport) -> str:
    """Markdown suitable for a PR comment or a CI artifact."""
    lines: List[str] = []
    lines.append(f"# Eval report ({report.client_label})")
    lines.append("")

    if not report.results:
        lines.append("No cases ran. (Did a filter match nothing?)")
        lines.append("")
        return "\n".join(lines)

    lines.append(
        f"**{report.passed}/{report.total} passed** "
        f"({report.pass_rate * 100:.1f}%) | "
        f"mean score {report.mean_score:.2f} | "
        f"{report.errored} errored | "
        f"{report.duration_s:.2f}s"
    )
    lines.append("")

    for layer, results in report.by_layer().items():
        n_pass = sum(1 for r in results if r.passed)
        lines.append(f"## Layer: {layer} ({n_pass}/{len(results)})")
        lines.append("")
        lines.append("| Case | Status | Score | Turns | Calls | Tools |")
        lines.append("| --- | --- | ---: | ---: | ---: | --- |")
        for r in results:
            lines.append(
                f"| {_md_escape(r.case_id)} "
                f"| {_status(r)} "
                f"| {r.score:.2f} "
                f"| {r.turns} "
                f"| {len(r.tool_calls)} "
                f"| {_md_escape(_tool_chain(r))} |"
            )
        lines.append("")

    failures = [r for r in report.results if not r.passed]
    if failures:
        lines.append("## Failures")
        lines.append("")
        for r in failures:
            lines.append(f"### {_md_escape(r.case_id)} ({r.layer}) - {_status(r)}")
            lines.append("")
            if r.error is not None:
                lines.append(f"- **crashed**: {_md_escape(_clamp(r.error, DETAIL_CLAMP))}")
            for name, detail in _failing_scores(r):
                lines.append(f"- **{_md_escape(name)}**: {_md_escape(_clamp(detail, DETAIL_CLAMP))}")
            if not r.scores and r.error is None:
                lines.append("- **no graders ran for this case**")
            lines.append(f"- tools: {_md_escape(_tool_chain(r))}")
            gt = r.ground_truth
            if gt is not None and gt.description:
                lines.append(f"- expected: {_md_escape(_clamp(gt.description, DETAIL_CLAMP))}")
            lines.append("")

    return "\n".join(lines)


# --------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------


def _result_to_dict(result: EvalResult) -> Dict[str, object]:
    gt = result.ground_truth
    return {
        "case_id": result.case_id,
        # Rounded: raw floats carry 17 digits of noise that make every commit
        # look like a score change.
        "duration_s": round(result.duration_s, 3),
        "error": result.error,
        "final_answer": _clamp(result.final_answer, ANSWER_CLAMP),
        "ground_truth": (
            None
            if gt is None
            else {
                "culprit_file": gt.culprit_file,
                "culprit_timestamp": gt.culprit_timestamp,
                "description": gt.description,
                "forbidden_keywords": list(gt.forbidden_keywords),
                "root_cause_keywords": list(gt.root_cause_keywords),
            }
        ),
        "layer": result.layer,
        "passed": result.passed,
        "score": round(result.score, 4),
        "scores": [
            {
                "detail": s.detail,
                "grader": s.grader,
                "passed": s.passed,
                "score": round(s.score, 4),
            }
            for s in result.scores
        ],
        "tool_calls": [
            {
                "input": c.get("input", {}),
                "is_error": bool(c.get("is_error", False)),
                "name": c.get("name", ""),
            }
            for c in result.tool_calls
        ],
        # The flat chain is redundant with tool_calls but is what you actually
        # read in a diff - a trajectory regression shows up as one changed line.
        "tool_chain": [n for n in result.tool_names if n],
        "tool_is_error": result.tool_is_error,
        "turns": result.turns,
    }


def render_json(report: SuiteReport) -> str:
    """
    Stable, diffable JSON.

    sort_keys plus rounded floats means two runs of an unchanged suite produce
    byte-identical output except for the timing fields. Timings are kept
    anyway - a case that suddenly takes 40x longer is a regression worth
    seeing - but they are the only field expected to churn, so a reviewer can
    ignore exactly one kind of diff line.

    Tool call *outputs* are deliberately excluded: they are large, they embed
    file paths, and the graders' detail strings already carry whatever part of
    them mattered.
    """
    payload = {
        "client_label": report.client_label,
        "duration_s": round(report.duration_s, 3),
        "results": [_result_to_dict(r) for r in report.results],
        "summary": {
            "by_layer": {
                layer: {
                    "mean_score": round(
                        sum(r.score for r in rs) / len(rs) if rs else 0.0, 4
                    ),
                    "passed": sum(1 for r in rs if r.passed),
                    "total": len(rs),
                }
                for layer, rs in report.by_layer().items()
            },
            "errored": report.errored,
            "failed": report.failed,
            "failed_ids": [r.case_id for r in report.results if not r.passed],
            "mean_score": round(report.mean_score, 4),
            "pass_rate": round(report.pass_rate, 4),
            "passed": report.passed,
            "total": report.total,
        },
    }
    return json.dumps(payload, sort_keys=True, indent=2) + "\n"


# --------------------------------------------------------------------------
# Writing to disk
# --------------------------------------------------------------------------


def write_reports(report: SuiteReport, out_dir: str) -> Dict[str, str]:
    """
    Write results.json and report.md into out_dir. Returns absolute paths.

    Fixed filenames rather than timestamped ones: the whole point of the JSON
    report is `git diff` across commits, and a name that changes every run
    makes that impossible.
    """
    out_path = os.path.abspath(out_dir)
    os.makedirs(out_path, exist_ok=True)

    json_path = os.path.join(out_path, "results.json")
    md_path = os.path.join(out_path, "report.md")

    with open(json_path, "w", encoding="utf-8") as f:
        f.write(render_json(report))
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(report))

    return {"json": json_path, "markdown": md_path}
