"""
Evaluation cases for the log agent harness.
"""

from .case import EvalCase
from .scenarios import get_scenario
from .graders import (
    KeywordGrader,
    ForbiddenKeywordGrader,
    ToolTrajectoryGrader,
    ToolOutputGrader,
    BrakeGrader,
)
from .replay import ScriptedClient  # noqa: F401  (scripts below are replayed by it)


def make_case(
    case_id: str,
    layer: str,
    scenario_name: str,
    prompt: str,
    rationale: str = "",
    tool_name: str = None,
    tool_input: dict = None,
    script: list = None,
    max_turns: int = 8,
    max_tool_calls: int = 12,
    max_output_tokens: int = 4000,
    graders: list = None,
) -> EvalCase:
    """Helper to create an EvalCase with common defaults.

    Explicit `graders` are kept as-is; the standard outcome-layer graders are
    only auto-added when none were supplied, so a case can always opt out.
    """
    scenario = get_scenario(scenario_name)
    case = EvalCase(
        id=case_id,
        layer=layer,
        scenario=scenario_name,
        prompt=prompt,
        rationale=rationale,
        tool_name=tool_name,
        tool_input=tool_input or {},
        script=script,
        max_turns=max_turns,
        max_tool_calls=max_tool_calls,
        max_output_tokens=max_output_tokens,
        graders=list(graders or []),
    )

    # Add standard graders based on scenario ground truth, unless the caller
    # already said exactly how this case should be scored.
    if layer == "outcome" and not case.graders:
        # Keyword grader for root cause
        case.graders.append(KeywordGrader(
            keywords=scenario.ground_truth.root_cause_keywords
        ))
        # Forbidden keyword grader for precision (if applicable)
        if scenario.ground_truth.forbidden_keywords:
            case.graders.append(ForbiddenKeywordGrader(
                keywords=scenario.ground_truth.forbidden_keywords
            ))
        # Tool trajectory grader to verify expected tools were used
        case.graders.append(ToolTrajectoryGrader(
            expected=scenario.ground_truth.expected_tools
        ))
        # Brake grader to ensure agent didn't hit limits prematurely
        case.graders.append(BrakeGrader())

    return case


# Tool Layer Cases - Direct tool testing
TOOL_CASES = [
    make_case(
        case_id="tool-list_logs",
        layer="tool",
        scenario_name="cascading_failure",
        prompt="",  # Not used for tool layer
        rationale="Test that list_logs tool works correctly",
        tool_name="list_logs",
        tool_input={},
        max_turns=1,
        max_tool_calls=1,
        graders=[
            ToolOutputGrader(
                must_contain=["deploy.log", "database.log", "service.log", "gateway.log"],
                min_lines=4,
            )
        ],
    ),
    make_case(
        case_id="tool-read_log",
        layer="tool",
        scenario_name="cascading_failure",
        prompt="",  # Not used for tool layer
        rationale="Test that read_log tool reads correct lines",
        tool_name="read_log",
        tool_input={
            "file": "deploy.log",
            "start_line": 1,
            "end_line": 5
        },
        max_turns=1,
        max_tool_calls=1,
        graders=[ToolOutputGrader(must_contain=["deploy.log"], min_lines=5)],
    ),
    make_case(
        case_id="tool-search_logs",
        layer="tool",
        scenario_name="cascading_failure",
        prompt="",  # Not used for tool layer
        rationale="Test that search_logs finds matching lines",
        tool_name="search_logs",
        tool_input={
            "pattern": "DB_POOL_SIZE",
            "file": "deploy.log"
        },
        max_turns=1,
        max_tool_calls=1,
        graders=[
            ToolOutputGrader(
                must_contain=["DB_POOL_SIZE increased from 10 to 100", "10:03:00"],
            )
        ],
    ),
    make_case(
        case_id="tool-timeline",
        layer="tool",
        scenario_name="cascading_failure",
        prompt="",  # Not used for tool layer
        rationale="Test that timeline tool merges and sorts logs",
        tool_name="timeline",
        tool_input={
            "files": ["deploy.log", "service.log", "gateway.log", "database.log"],
            "around": "2026-08-24 10:03:00",
            "window": "00:02:00",
        },
        max_turns=1,
        max_tool_calls=1,
        # The timestamp-parsing bug this suite exists to catch shows up here:
        # a timeline that silently returns nothing still "succeeds". Assert on
        # the merge itself (>1 file, in order), not on any one line - the 500
        # char output budget clamps the tail away.
        graders=[
            ToolOutputGrader(
                must_contain=["deploy.log", "service.log", "2026-08-24 10:0"],
                min_lines=3,
            )
        ],
    ),
]

# Trajectory Layer Cases - Scripted LLM to check tool usage
TRAJECTORY_CASES = [
    make_case(
        case_id="traj-cascading_failure_tool_usage",
        layer="trajectory",
        scenario_name="cascading_failure",
        prompt="Investigate the 502 errors in the gateway logs",
        rationale="Check that agent uses appropriate tools to investigate cascading failure",
        max_turns=5,
        max_tool_calls=8,
        script=[
            {"tools": [{"name": "list_logs", "input": {}}]},
            {
                "tools": [
                    {
                        "name": "search_logs",
                        "input": {"pattern": "502", "file": "gateway.log"},
                    }
                ]
            },
            {
                "tools": [
                    {
                        "name": "timeline",
                        "input": {
                            "files": ["deploy.log", "database.log", "gateway.log"],
                            "around": "2026-08-24 10:03:00",
                            "window": "00:02:00",
                        },
                    }
                ]
            },
            {"text": "The 2.3.0 deploy raised DB_POOL_SIZE from 10 to 100."},
        ],
        graders=[
            ToolTrajectoryGrader(expected=["search_logs", "timeline"]),
            BrakeGrader(),
        ],
    ),
]

# Outcome Layer Cases - Live LLM to check final diagnosis
OUTCOME_CASES = [
    make_case(
        case_id="outcome-cascading_failure",
        layer="outcome",
        scenario_name="cascading_failure",
        prompt="What is the root cause of the 502 errors seen in the gateway logs around 10:03-10:05?",
        rationale="Test end-to-end ability to diagnose cascading failure scenario",
    ),
    make_case(
        case_id="outcome-disk_full",
        layer="outcome",
        scenario_name="disk_full",
        prompt="Why did write operations start failing around 10:04? Check storage logs for errors.",
        rationale="Test ability to diagnose disk full scenario from storage logs",
    ),
    make_case(
        case_id="outcome-memory_leak",
        layer="outcome",
        scenario_name="memory_leak",
        prompt="What caused the restart loop in worker-3 around 10:05? Look for memory-related issues.",
        rationale="Test ability to diagnose memory leak scenario from worker logs",
    ),
    make_case(
        case_id="outcome-cert_expiry",
        layer="outcome",
        scenario_name="cert_expiry",
        prompt="Why did the gateway start returning 503 errors around 10:02? Check TLS logs.",
        rationale="Test ability to diagnose certificate expiry scenario from TLS logs",
    ),
    make_case(
        case_id="outcome-red_herring",
        layer="outcome",
        scenario_name="red_herring",
        prompt="Investigate the 500 errors in the gateway logs around 10:03. Is the cache layer to blame?",
        rationale="Test ability to ignore misleading cache metrics and find real thread pool issue",
    ),
]

# Skill Layer Cases - the playbooks in ./skills, scored as tools
#
# These only run when the runner is given --skills-root; without it the two
# skill tools are not registered and the cases correctly report an unknown
# tool. That is deliberate: skills are opt-in, and a case that passed either
# way would not be testing the wiring.
SKILL_CASES = [
    make_case(
        case_id="tool-list_skills",
        layer="tool",
        scenario_name="cascading_failure",
        prompt="",
        rationale="The skill index must name every playbook and its trigger",
        tool_name="list_skills",
        tool_input={},
        max_turns=1,
        max_tool_calls=1,
        graders=[
            ToolOutputGrader(
                must_contain=["cascading-failure", "resource-exhaustion", "load_skill"],
                min_lines=4,
            )
        ],
    ),
    make_case(
        case_id="tool-load_skill",
        layer="tool",
        scenario_name="cascading_failure",
        prompt="",
        rationale="Loading a playbook must return its procedure, not just its title",
        tool_name="load_skill",
        tool_input={"name": "cascading-failure"},
        max_turns=1,
        max_tool_calls=1,
        graders=[ToolOutputGrader(must_contain=["timeline", "first"], min_lines=5)],
    ),
    make_case(
        case_id="tool-load_skill-unknown",
        layer="tool",
        scenario_name="cascading_failure",
        prompt="",
        rationale="An unknown skill must be an error result that names the alternatives",
        tool_name="load_skill",
        tool_input={"name": "no-such-skill"},
        max_turns=1,
        max_tool_calls=1,
        # The negative control: a tool that fails helpfully is what keeps the
        # agent from burning a turn guessing at names.
        graders=[
            ToolOutputGrader(
                expect_error=True,
                must_contain=["cascading-failure"],
            )
        ],
    ),
]

# Combine all cases
ALL_CASES = TOOL_CASES + SKILL_CASES + TRAJECTORY_CASES + OUTCOME_CASES

# Export for use by runner
__all__ = [
    'ALL_CASES',
    'TOOL_CASES',
    'SKILL_CASES',
    'TRAJECTORY_CASES',
    'OUTCOME_CASES',
]