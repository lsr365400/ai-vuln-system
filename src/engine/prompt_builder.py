import re
from fnmatch import fnmatch
from pathlib import Path
from urllib.parse import urlparse


def load_skill_file(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"核心技能文件不存在: {path}")
    return path.read_text(encoding="utf-8")


def load_scenario_rules(scenario: str, scenarios_dir: Path) -> str:
    rule_file = scenarios_dir / f"{scenario}-rules.md"
    if rule_file.exists():
        return rule_file.read_text(encoding="utf-8")
    return ""


def compile_target_pattern(target_url: str) -> str:
    """Convert user input (URL, wildcard, or comma-separated list) to a regex pattern."""
    parts = [p.strip() for p in target_url.split(",") if p.strip()]
    patterns = []
    for p in parts:
        if "*" in p:
            escaped = re.escape(p).replace(r"\*", ".*?")
            if not p.startswith("http"):
                escaped = r"https?://" + escaped
            patterns.append(escaped)
        else:
            if p.startswith("http"):
                parsed = urlparse(p)
                patterns.append(re.escape(p.rstrip("/")))
            else:
                p = p.rstrip("/")
                patterns.append(re.escape(f"https://{p}"))
                patterns.append(re.escape(f"http://{p}"))
    return "^(?:" + "|".join(patterns) + ")"


def is_cross_domain(target_url: str) -> bool:
    """Check if the target URL represents a range (wildcard or multiple URLs)."""
    return "*" in target_url or "," in target_url


def url_matches_pattern(url: str, pattern_re: str) -> bool:
    """Check if a URL matches the compiled target pattern."""
    return bool(re.match(pattern_re, url))


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

    cross_domain = is_cross_domain(target_url)
    domain_info = ""
    if cross_domain:
        domain_list = target_url if "," not in target_url else ", ".join(target_url.split(","))
        domain_info = f"""
## 测试范围（跨域模式）

你被授权测试以下域名范围: **{target_url}**

- 可以向匹配此范围的所有域名发送请求——不受单目标限制
- 发现子域名/关联域名/API 子域时，只要匹配此范围即可直接测试
- 例如: `*.edu.cn` 允许测试 `www.example.edu.cn`、`api.example.edu.cn`、`login.example.edu.cn` 等所有子域
- 报告时注明具体域名，不要笼统写范围
"""
    else:
        domain_info = f"\n## 当前会话信息\n\n- 目标: {target_url}\n- 场景: {scenario}\n"

    session_info = f"""
- 临时目录: {temp_dir}
- 报告目录: {report_dir}
- 工具: curl_http, exec_shell, write_report, finish_session

所有文件操作限制在临时目录内。报告写入报告目录。
"""

    parts = [core]
    if scenario_rules:
        parts.append(scenario_rules)
    parts.append(domain_info + session_info)
    return "\n\n".join(parts)
