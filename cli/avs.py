#!/usr/bin/env python3
"""AI Vulnerability Scanner — CLI entry point (MVP)"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_settings
from src.engine.session import run_session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger("avs")


def main():
    parser = argparse.ArgumentParser(
        description="AI 辅助漏洞扫描器 (MVP)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  avs https://target.edu.cn
  avs https://target.edu.cn -s edu -p 10
  avs https://target.edu.cn -s custom --project "my-project"
        """,
    )
    parser.add_argument("target", help="目标 URL")
    parser.add_argument("-s", "--scenario", default="custom",
                        choices=["custom", "edu", "src"],
                        help="测试场景 (default: custom)")
    parser.add_argument("-p", "--priority", type=int, default=5,
                        help="优先级 1-10 (default: 5)")
    parser.add_argument("--project", default="default",
                        help="项目标识 (default: default)")
    parser.add_argument("--env", default=".env",
                        help="环境变量文件路径 (default: .env)")

    args = parser.parse_args()

    # 加载配置
    settings = load_settings()

    if not settings.deepseek_api_key:
        logger.error("未配置 DEEPSEEK_API_KEY。请在 .env 文件中设置。")
        logger.error("  cp .env.example .env  &&  编辑 .env 填入 API Key")
        sys.exit(1)

    logger.info(f"目标: {args.target}")
    logger.info(f"场景: {args.scenario}")
    logger.info(f"模型: {settings.deepseek_model}")
    logger.info(f"报告目录: {settings.report_dir.resolve()}")
    logger.info(f"临时目录: {settings.session_dir.resolve()}")
    logger.info("=" * 60)

    # 运行会话
    final_status = asyncio.run(run_session(
        settings=settings,
        target_url=args.target,
        scenario=args.scenario,
        project_id=args.project,
        priority=args.priority,
    ))

    logger.info("=" * 60)
    logger.info(f"终态: {final_status}")

    if final_status == "vuln_found":
        logger.info("发现漏洞！查看 data/reports/ 目录")
    elif final_status == "low_roi":
        logger.info("本轮未发现符合报告标准的漏洞")
    elif final_status == "need_input":
        logger.info("AI 需要更多信息才能继续")
    else:
        logger.error("会话异常终止")

    sys.exit(0 if final_status in ("vuln_found", "low_roi") else 1)


if __name__ == "__main__":
    main()
