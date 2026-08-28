"""
Tests for the skill layer. No API key, no network.

The interesting cases are the malformed ones: a skill system is only useful if
a bad playbook degrades instead of exploding, because playbooks are the part of
the system non-programmers will edit.
"""

import os

from logagent.skills import SkillLibrary, parse_skill, register_skill_tools
from logagent.tools import ToolRegistry


def _write(root, name, text):
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "SKILL.md"), "w") as f:
        f.write(text)


GOOD = """---
name: demo
description: A demo playbook.
tools: search_logs, timeline
---

## Body

Step one.
"""


def test_parses_frontmatter():
    skill = parse_skill(GOOD, path="/x/demo/SKILL.md")
    assert skill.name == "demo"
    assert skill.description == "A demo playbook."
    assert skill.tools == ["search_logs", "timeline"]
    assert "Step one." in skill.body
    assert "---" not in skill.body  # frontmatter stripped, not left in the body


def test_no_frontmatter_falls_back_to_dir_name_and_first_line():
    skill = parse_skill("# Just a note\n\nbody", path="/x/notes/SKILL.md")
    assert skill.name == "notes"
    assert skill.description == "Just a note"
    assert skill.tools == []


def test_missing_root_is_empty_not_an_error(tmp_path):
    lib = SkillLibrary(skills_root=str(tmp_path / "nope"))
    assert lib.list_skills_sorted() == []
    result = lib.list_skills({})
    assert result["is_error"] is False
    assert "No skills found" in result["content"][0]["text"]


def test_discovery_and_load(tmp_path):
    _write(str(tmp_path), "demo", GOOD)
    lib = SkillLibrary(skills_root=str(tmp_path))

    listing = lib.list_skills({})["content"][0]["text"]
    assert "demo: A demo playbook." in listing
    assert "search_logs" in listing

    loaded = lib.load_skill({"name": "demo"})
    assert loaded["is_error"] is False
    assert "Step one." in loaded["content"][0]["text"]


def test_unknown_skill_is_an_error_result_naming_the_alternatives(tmp_path):
    _write(str(tmp_path), "demo", GOOD)
    lib = SkillLibrary(skills_root=str(tmp_path))

    result = lib.load_skill({"name": "missing"})
    assert result["is_error"] is True
    # The error has to tell the model what to try instead, or it burns a turn
    # guessing at names.
    assert "demo" in result["content"][0]["text"]

    assert lib.load_skill({})["is_error"] is True


def test_body_is_clamped_with_an_explicit_notice(tmp_path):
    _write(str(tmp_path), "big", "---\nname: big\ndescription: d\n---\n" + "x" * 5000)
    lib = SkillLibrary(skills_root=str(tmp_path), output_char_budget=500)

    text = lib.load_skill({"name": "big"})["content"][0]["text"]
    assert len(text) < 800
    assert "not shown" in text  # never truncate silently


def test_validate_flags_tools_that_are_not_registered(tmp_path):
    _write(str(tmp_path), "demo", GOOD)
    lib = SkillLibrary(skills_root=str(tmp_path))

    assert lib.validate(["search_logs", "timeline"]) == []
    problems = lib.validate(["search_logs"])
    assert len(problems) == 1
    assert "timeline" in problems[0]


def test_duplicate_names_are_reported_not_silently_shadowed(tmp_path):
    _write(str(tmp_path), "a", GOOD)
    _write(str(tmp_path), "b", GOOD)  # same `name: demo` in the frontmatter
    lib = SkillLibrary(skills_root=str(tmp_path))

    assert len(lib.list_skills_sorted()) == 1
    assert any("duplicate" in e for e in lib.load_errors)


def test_register_skill_tools_wires_both_tools(tmp_path):
    _write(str(tmp_path), "demo", GOOD)
    registry = ToolRegistry()
    register_skill_tools(registry, SkillLibrary(skills_root=str(tmp_path)))

    names = [t["name"] for t in registry.list_tools()]
    assert names == ["list_skills", "load_skill"]

    out = registry.execute_tool("load_skill", {"name": "demo"})
    assert "Step one." in out["content"][0]["text"]


def test_shipped_skills_are_valid():
    """The playbooks in ./skills must parse and name only real tools."""
    root = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skills")
    lib = SkillLibrary(skills_root=root)

    assert lib.load_errors == []
    assert len(lib.list_skills_sorted()) >= 1
    real_tools = ["list_logs", "read_log", "search_logs", "log_stats", "timeline"]
    assert lib.validate(real_tools) == []
    for skill in lib.list_skills_sorted():
        assert skill.description and skill.description != "(no description)"
