from pathlib import Path


def load_skill_file(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"核心技能文件不存在: {path}")
    return path.read_text(encoding="utf-8")


def load_scenario_rules(scenario: str, scenarios_dir: Path) -> str:
    rule_file = scenarios_dir / f"{scenario}-rules.md"
    if rule_file.exists():
        return rule_file.read_text(encoding="utf-8")
    return ""


def build_system_prompt(
    core_skill_path: Path,
    target_url: str,
    scenario: str,
    scenarios_dir: Path,
    temp_dir: Path,
    report_dir: Path,
) -> str:
    core = load_skill_file(core_skill_path)
    scenario_rules = load_scenario_rules(scenario, scenarios_dir)

    session_info = f"""
## 当前会话信息

- 目标: {target_url}
- 场景: {scenario}
- 临时目录: {temp_dir}
- 报告目录: {report_dir}
- 工具: curl_http, exec_shell, write_report, finish_session

所有文件操作限制在临时目录内。报告写入报告目录。
"""

    parts = [core]
    if scenario_rules:
        parts.append(scenario_rules)
    parts.append(session_info)
    return "\n\n".join(parts)
