import tempfile
from pathlib import Path
from src.engine.prompt_builder import (
    load_skill_file,
    load_scenario_rules,
    build_system_prompt,
)


def test_load_skill_file(tmp_path):
    skill = tmp_path / "core-skill.md"
    skill.write_text("# Test skill", encoding="utf-8")
    content = load_skill_file(skill)
    assert content == "# Test skill"


def test_load_skill_file_not_found():
    try:
        load_skill_file(Path("/nonexistent/skill.md"))
        assert False, "应该抛出异常"
    except FileNotFoundError:
        pass


def test_load_scenario_rules(tmp_path):
    scenarios_dir = tmp_path / "scenarios"
    scenarios_dir.mkdir()
    (scenarios_dir / "edu-rules.md").write_text("# edu rules", encoding="utf-8")
    result = load_scenario_rules("edu", scenarios_dir)
    assert result == "# edu rules"


def test_load_scenario_rules_not_found(tmp_path):
    result = load_scenario_rules("nonexistent", tmp_path)
    assert result == ""


def test_build_system_prompt(tmp_path):
    skill = tmp_path / "core-skill.md"
    skill.write_text("CORE SKILL", encoding="utf-8")
    scenarios_dir = tmp_path / "scenarios"
    scenarios_dir.mkdir()
    (scenarios_dir / "edu-rules.md").write_text("EDU RULES", encoding="utf-8")
    temp_dir = tmp_path / "sessions" / "test-session"
    temp_dir.mkdir(parents=True)
    report_dir = tmp_path / "reports"
    report_dir.mkdir()

    prompt = build_system_prompt(
        core_skill_path=skill,
        target_url="https://example.edu.cn",
        scenario="edu",
        scenarios_dir=scenarios_dir,
        temp_dir=temp_dir,
        report_dir=report_dir,
    )

    assert "CORE SKILL" in prompt
    assert "EDU RULES" in prompt
    assert "https://example.edu.cn" in prompt
    assert str(temp_dir) in prompt
