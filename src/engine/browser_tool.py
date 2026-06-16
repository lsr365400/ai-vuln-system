"""Browser MCP — Playwright-based browser automation for login state maintenance.

Each session gets its own browser context with isolated cookie jar.
All cookies, localStorage, and sessions persist across calls within the same context.
"""

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Lazy-init: Playwright is heavy, only import when first used
_browser = None
_playwright = None


async def _get_browser():
    global _browser, _playwright
    if _browser is None:
        try:
            from playwright.async_api import async_playwright
            _playwright = await async_playwright().start()
            _browser = await _playwright.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-gpu"]
            )
            logger.info("playwright browser launched")
        except Exception as e:
            logger.warning("browser launch failed: %s", e)
            return None
    return _browser


# Per-session contexts: session_id → BrowserContext
_contexts: dict[str, Any] = {}


async def get_context(session_id: str, temp_dir: Path) -> Any:
    """Get or create an isolated browser context for this session."""
    browser = await _get_browser()
    if browser is None:
        return None
    if session_id not in _contexts:
        user_data = temp_dir / "browser_profile"
        user_data.mkdir(parents=True, exist_ok=True)
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
            locale="zh-CN",
        )
        _contexts[session_id] = ctx
    return _contexts[session_id]


async def cleanup_context(session_id: str) -> None:
    ctx = _contexts.pop(session_id, None)
    if ctx:
        await ctx.close()


async def browser_navigate(session_id: str, temp_dir: Path, args: dict) -> dict:
    """Navigate to a URL and return full rendered page info."""
    ctx = await get_context(session_id, temp_dir)
    if ctx is None:
        return {"error": "浏览器不可用（chromium 未安装），请改用 curl_http"}
    page = await ctx.new_page()
    try:
        await page.goto(args["url"], wait_until="networkidle", timeout=args.get("timeout", 30000))
        html = await page.content()
        title = await page.title()
        url_final = page.url

        # Extract links, forms, scripts from rendered DOM
        hrefs = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
        form_actions = await page.eval_on_selector_all("form[action]", "els => els.map(e => e.action)")
        scripts = await page.eval_on_selector_all("script[src]", "els => els.map(e => e.src)")
        # Check for login form presence
        has_login_form = await page.eval_on_selector("form input[type=password]", "e => !!e").catch(lambda: False)

        return {
            "url": url_final,
            "title": title,
            "html_length": len(html),
            "html": html[:500] + f"\n... ({len(html)} chars total, use browser_extract for specific elements)" if len(html) > 500 else html,
            "links": hrefs[:30],
            "form_actions": form_actions[:10],
            "scripts": scripts[:15],
            "has_login_form": has_login_form,
            "cookies": await ctx.cookies(),
        }
    finally:
        await page.close()


async def browser_login(session_id: str, temp_dir: Path, args: dict) -> dict:
    """Fill login form, submit, and verify authentication."""
    ctx = await get_context(session_id, temp_dir)
    if ctx is None:
        return {"error": "浏览器不可用（chromium 未安装），请改用 curl_http"}
    page = await ctx.new_page()
    try:
        # Navigate to login page
        await page.goto(args["url"], wait_until="networkidle", timeout=args.get("timeout", 30000))
        login_url = page.url

        # Fill fields
        username_field = args.get("username_field", "input[name=username]")
        password_field = args.get("password_field", "input[name=password]")
        await page.fill(username_field, args["username"])
        await page.fill(password_field, args["password"])

        # Click submit
        submit_btn = args.get("submit_button", "input[type=submit], button[type=submit]")
        await page.click(submit_btn)

        # Wait for navigation
        await page.wait_for_load_state("networkidle", timeout=15000)

        current_url = page.url
        current_title = await page.title()
        html = await page.content()

        # Determine login success
        redirect_away = current_url != login_url and "login" not in current_url.lower()
        has_logout = "logout" in html.lower() or "退出" in html
        has_welcome = "welcome" in html.lower() or "欢迎" in html.lower() or "dashboard" in html.lower()
        has_login_form = await page.eval_on_selector("form input[type=password]", "e => !!e").catch(lambda: False)

        authenticated = (redirect_away and (has_logout or has_welcome)) or (has_welcome and not has_login_form)

        return {
            "authenticated": authenticated,
            "evidence": f"title='{current_title}', url='{current_url}', logout_found={has_logout}, welcome_found={has_welcome}, has_password_field={has_login_form}",
            "final_url": current_url,
            "title": current_title,
            "cookies": await ctx.cookies(),
            "html_snippet": html[:300],
        }
    finally:
        await page.close()


async def browser_extract(session_id: str, temp_dir: Path, args: dict) -> dict:
    """Extract content from a currently-loaded or new page. Use after browser_navigate to read specific elements."""
    ctx = await get_context(session_id, temp_dir)
    if ctx is None:
        return {"error": "浏览器不可用（chromium 未安装），请改用 curl_http"}
    page = await ctx.new_page()
    try:
        if args.get("url"):
            await page.goto(args["url"], wait_until="networkidle", timeout=args.get("timeout", 30000))

        results = {}
        # Extract full HTML if requested
        if args.get("get_html"):
            results["html"] = await page.content()

        # Extract by CSS selector
        if args.get("selector"):
            els = await page.eval_on_selector_all(
                args["selector"],
                "els => els.map(e => ({ text: e.textContent?.trim()?.substring(0,500), href: e.href, src: e.src }))"
            )
            results["elements"] = els[:20]

        # Extract specific text
        if args.get("contains"):
            text = await page.eval_on_selector_all(
                f"text='{args['contains']}'",
                "els => els.map(e => e.textContent?.trim()?.substring(0,300))"
            )
            results["matching_text"] = text[:10]

        return results
    finally:
        await page.close()


async def execute_browser_tool(session_id: str, temp_dir: Path, tool_call: dict) -> dict[str, Any]:
    """Route browser tool calls."""
    name = tool_call["function"]["name"]
    args = tool_call["function"].get("arguments_parsed", {})

    if name == "browser_navigate":
        result = await browser_navigate(session_id, temp_dir, args)
    elif name == "browser_login":
        result = await browser_login(session_id, temp_dir, args)
    elif name == "browser_extract":
        result = await browser_extract(session_id, temp_dir, args)
    else:
        result = {"error": f"Unknown browser tool: {name}"}

    return {
        "tool_call_id": tool_call["id"],
        "role": "tool",
        "content": str(result),
    }
