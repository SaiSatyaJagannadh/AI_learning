"""
Skills for the agent harness: procedural knowledge the agent loads on demand.

Why this module exists
----------------------
The harness gives the agent *capabilities* (tools) and the eval suite measures
*outcomes*. Neither one carries **procedure** - the knowledge that a 502 storm
is diagnosed by finding the first bad timestamp in the gateway and then walking
the timeline backwards through every other log. That knowledge has to live
somewhere, and there are only three places to put it:

  1. In the system prompt. Free at call time, but every skill you add taxes
     every request forever, including the ones that need none of them.
  2. In the model weights. Not available to us.
  3. On disk, loaded on demand. Costs one tool call when it is actually needed
     and nothing at all when it is not.

This module is option 3, and it is the same trade the log tools make: the agent
does not read every log file up front, it lists them and opens the one that
matters. A skill is a markdown playbook the agent lists cheaply and reads only
when the description matches the problem in front of it.

The shape of a skill
--------------------
One directory, one `SKILL.md`, YAML-ish frontmatter and a markdown body:

    ---
    name: cascading-failure
    description: Trace a downstream error storm back to the upstream change.
    tools: search_logs, timeline
    ---

    ## When to use this
    ...

The frontmatter is the *index*: name and description are all the agent sees
when it lists skills, and they are the entire basis on which it decides to
spend a turn loading the body. A vague description is therefore not a
documentation problem, it is a retrieval bug - the skill will never be picked.

The parser here is deliberately hand-rolled and about twenty lines. Adding a
YAML dependency to read four scalar keys is the kind of thing that looks
harmless and then owns your install story. If frontmatter ever needs nesting,
that is the moment to reach for PyYAML - not before.
"""

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# A skill folder is recognised by this file and nothing else. One well-known
# filename means discovery is a directory walk, not a config format.
SKILL_FILE = "SKILL.md"

# Same treatment the log tools give their output: a skill body is model input
# like any other, and an unbounded one silently eats the context window that
# the actual logs need.
DEFAULT_SKILL_CHAR_BUDGET = 4000

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


@dataclass
class Skill:
    """One playbook: its index entry and its body."""

    name: str
    description: str
    body: str
    path: str

    # Tools this skill expects to exist. Purely advisory to the model, but it
    # is also what `SkillLibrary.validate` checks against the real registry, so
    # a playbook that references a tool nobody registered fails loudly at
    # startup instead of halfway through an investigation.
    tools: List[str] = field(default_factory=list)

    def index_entry(self) -> str:
        """The one-line form the agent sees in `list_skills`."""
        line = f"{self.name}: {self.description}"
        if self.tools:
            line += f" (uses: {', '.join(self.tools)})"
        return line


def parse_skill(text: str, path: str = "") -> Skill:
    """
    Parse a SKILL.md into a Skill.

    A file with no frontmatter is not an error: its name falls back to the
    directory name and its description to the first non-empty line. Being
    lenient here means a plain markdown note dropped into the skills directory
    still works, which is what makes the format worth using.
    """
    match = _FRONTMATTER_RE.match(text)
    meta: Dict[str, str] = {}
    body = text

    if match:
        raw_meta, body = match.group(1), match.group(2)
        for line in raw_meta.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, _, value = line.partition(":")
            meta[key.strip().lower()] = value.strip().strip("'\"")

    name = meta.get("name") or _fallback_name(path)
    description = meta.get("description") or _first_line(body)
    tools = [t.strip() for t in meta.get("tools", "").split(",") if t.strip()]

    return Skill(
        name=name,
        description=description,
        body=body.strip(),
        path=path,
        tools=tools,
    )


def _fallback_name(path: str) -> str:
    if not path:
        return "unnamed"
    return os.path.basename(os.path.dirname(os.path.abspath(path))) or "unnamed"


def _first_line(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped
    return "(no description)"


class SkillLibrary:
    """
    Discovers skills under a root directory and serves them as two tools.

    Two tools, not one, for the same reason the log tools are `list_logs` plus
    `read_log`: listing is cheap and always safe to call, reading costs context
    and should be a decision the model makes deliberately.
    """

    def __init__(
        self,
        skills_root: str,
        output_char_budget: int = DEFAULT_SKILL_CHAR_BUDGET,
    ):
        self.skills_root = skills_root
        self.output_char_budget = output_char_budget
        self._skills: Dict[str, Skill] = {}
        self.load_errors: List[str] = []
        self.reload()

    # -- discovery ------------------------------------------------------

    def reload(self) -> None:
        """
        Re-scan the skills root. Safe to call on a missing directory.

        A broken skill file is recorded in `load_errors` rather than raised: one
        unreadable playbook should not stop an agent that was going to use a
        different one.
        """
        self._skills = {}
        self.load_errors = []
        if not os.path.isdir(self.skills_root):
            return

        for dirpath, _dirnames, filenames in os.walk(self.skills_root):
            if SKILL_FILE not in filenames:
                continue
            path = os.path.join(dirpath, SKILL_FILE)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    skill = parse_skill(f.read(), path=path)
            except (OSError, UnicodeDecodeError) as e:
                self.load_errors.append(f"{path}: {e}")
                continue
            if skill.name in self._skills:
                self.load_errors.append(
                    f"{path}: duplicate skill name {skill.name!r}, keeping "
                    f"{self._skills[skill.name].path}"
                )
                continue
            self._skills[skill.name] = skill

    def list_skills_sorted(self) -> List[Skill]:
        return sorted(self._skills.values(), key=lambda s: s.name)

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def validate(self, registry_tool_names: List[str]) -> List[str]:
        """
        Report skills naming tools that are not registered.

        Called once at startup by cli.py. A playbook that tells the model to
        call `grep_logs` when the tool is named `search_logs` produces an agent
        that confidently calls a tool that does not exist, and the failure
        surfaces three turns later as a confused transcript.
        """
        known = set(registry_tool_names)
        problems = []
        for skill in self.list_skills_sorted():
            missing = [t for t in skill.tools if t not in known]
            if missing:
                problems.append(
                    f"skill {skill.name!r} references unregistered tool(s): "
                    + ", ".join(missing)
                )
        return problems

    # -- tools ----------------------------------------------------------

    def list_skills(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        List available skills.
        Args: none
        Returns: one "name: description" line per skill.
        """
        skills = self.list_skills_sorted()
        if not skills:
            # An empty result with advice beats an empty result. The model must
            # be able to tell "no skills installed" from "the tool is broken".
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"No skills found under {self.skills_root!r}. "
                            "Proceed without a playbook."
                        ),
                    }
                ],
                "is_error": False,
            }

        lines = [f"{len(skills)} skill(s) available:"]
        lines += [f"  {s.index_entry()}" for s in skills]
        lines.append("Call load_skill with a name to read the full playbook.")
        return {
            "content": [{"type": "text", "text": "\n".join(lines)}],
            "is_error": False,
        }

    def load_skill(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Load the full text of one skill.
        Args:
          - name: str (required)  // skill name as shown by list_skills
        Returns: the playbook body, clamped to the character budget.
        """
        name = (args.get("name") or "").strip()
        if not name:
            return self._error(
                "Error: 'name' is required. Call list_skills first to see the "
                "available names."
            )

        skill = self._skills.get(name)
        if skill is None:
            available = ", ".join(s.name for s in self.list_skills_sorted())
            return self._error(
                f"Error: no skill named {name!r}. "
                + (f"Available: {available}." if available else "No skills installed.")
            )

        text = skill.body
        header = f"# Skill: {skill.name}\n{skill.description}\n\n"
        # Clamp with the same honesty rule the log tools follow: say what was
        # held back and where to get it, never truncate silently.
        budget = max(0, self.output_char_budget - len(header))
        if len(text) > budget:
            held = len(text) - budget
            text = (
                text[:budget]
                + f"\n\n[{held} chars not shown - full playbook at {skill.path}]"
            )

        return {
            "content": [{"type": "text", "text": header + text}],
            "is_error": False,
        }

    @staticmethod
    def _error(message: str) -> Dict[str, Any]:
        return {"content": [{"type": "text", "text": message}], "is_error": True}


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------

LIST_SKILLS_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

LOAD_SKILL_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Skill name exactly as returned by list_skills.",
        }
    },
    "required": ["name"],
    "additionalProperties": False,
}


def register_skill_tools(registry, library: "SkillLibrary") -> None:
    """
    Register list_skills and load_skill on an existing ToolRegistry.

    Kept here rather than in cli.py so the eval suite can build a registry with
    skills using the same one line the CLI uses - if the two ever drift, the
    suite stops measuring the agent that actually ships.
    """
    registry.register_tool(
        name="list_skills",
        description=(
            "List available diagnostic playbooks (skills) by name and "
            "description. Call this first when starting an unfamiliar "
            "investigation - a matching playbook saves several turns of "
            "guessing."
        ),
        parameters=LIST_SKILLS_SCHEMA,
        function=library.list_skills,
        dangerous=False,
    )
    registry.register_tool(
        name="load_skill",
        description=(
            "Read the full text of one playbook returned by list_skills. "
            "Load a skill only when its description matches the problem; "
            "each one costs context."
        ),
        parameters=LOAD_SKILL_SCHEMA,
        function=library.load_skill,
        dangerous=False,
    )
