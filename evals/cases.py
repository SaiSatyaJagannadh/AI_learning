"""
Evaluation cases for the log agent harness.
"""

from .case import EvalCase
from .scenarios import get_scenario
from .graders import KeywordGrader, ForbiddenKeywordGrader, ToolTrajectoryGrader, BrakeGrader


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
) -> EvalCase:
    """Helper to create an EvalCase with common defaults."""
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
        graders=[],  # Will be populated below
    )

    # Add standard graders based on scenario ground truth
    if layer == "outcome":
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
            "start_time": "2026-08-24 10:00:00",
            "end_time": "2026-08-24 10:10:00"
        },
        max_turns=1,
        max_tool_calls=1,
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

# Combine all cases
ALL_CASES = TOOL_CASES + TRAJECTORY_CASES + OUTCOME_CASES

# Export for use by runner
__all__ = ['ALL_CASES', 'TOOL_CASES', 'TRAJECTORY_CASES', 'OUTCOME_CASES']