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
        try:
            has_login_form = await page.eval_on_selector("form input[type=password]", "e => !!e") or False
        except Exception:
            has_login_form = False

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


USERNAME_SELECTORS = [
    "input[name=username]",
    "input[name=email]",
    "input[type=email]",
    "input[autocomplete=username]",
    "input[type=text][autocomplete=email]",
    "input[placeholder*=邮箱]",
    "input[placeholder*=手机]",
    "input[placeholder*=账号]",
    "input[type=text]:not([readonly]):not([type=hidden])",
]

PASSWORD_SELECTORS = [
    "input[name=password]",
    "input[type=password]",
    "input[placeholder*=密码]",
]

SUBMIT_SELECTORS = [
    "input[type=submit]",
    "button[type=submit]",
    "button:has-text('登录')",
    "button:has-text('登 录')",
    "button:has-text('Log in')",
    "button:has-text('Sign in')",
    "[role=button]:has-text('登录')",
]


async def _try_find_selector(page, selectors: list[str], timeout: int = 3000) -> str | None:
    """Try each selector, return the first one that matches."""
    for sel in selectors:
        try:
            await page.wait_for_selector(sel, timeout=timeout, state="attached")
            if await page.locator(sel).count() > 0:
                return sel
        except Exception:
            continue
    return None


async def browser_login(session_id: str, temp_dir: Path, args: dict) -> dict:
    """Fill login form, submit, and verify authentication."""
    ctx = await get_context(session_id, temp_dir)
    if ctx is None:
        return {"error": "浏览器不可用（chromium 未安装），请改用 curl_http"}
    page = await ctx.new_page()
    try:
        # Navigate to login page
        await page.goto(args["url"], wait_until="networkidle", timeout=args.get("timeout", 20000))
        login_url = page.url

        # --- Flexible username/password selector detection ---
        custom_user = args.get("username_field")
        custom_pass = args.get("password_field")
        user_selectors = [custom_user] + USERNAME_SELECTORS if custom_user else USERNAME_SELECTORS
        pass_selectors = [custom_pass] + PASSWORD_SELECTORS if custom_pass else PASSWORD_SELECTORS

        username_sel = await _try_find_selector(page, user_selectors)
        password_sel = await _try_find_selector(page, pass_selectors)

        if not username_sel or not password_sel:
            text_inputs = page.locator("input[type=text]:visible, input[type=email]:visible, input:not([type])")
            pw_inputs = page.locator("input[type=password]:visible")
            text_count = await text_inputs.count()
            pw_count = await pw_inputs.count()
            if text_count > 0 and pw_count > 0:
                username_sel = "input[type=text]:visible, input[type=email]:visible, input:not([type])"
                password_sel = "input[type=password]:visible"
                await text_inputs.first.fill(args["username"])
                await pw_inputs.first.fill(args["password"])
            else:
                return {
                    "authenticated": False,
                    "error": f"找不到登录表单字段。页面上 text 输入框 {text_count} 个，password 输入框 {pw_count} 个。请用 browser_navigate 先查看页面结构，或手动指定 username_field/password_field。",
                    "page_title": await page.title(),
                    "html_snippet": (await page.content())[:1000],
                }

        if username_sel and password_sel:
            try:
                await page.fill(username_sel, args["username"])
            except Exception:
                await page.locator(username_sel).first.fill(args["username"])
            try:
                await page.fill(password_sel, args["password"])
            except Exception:
                await page.locator(password_sel).first.fill(args["password"])

        # Click submit or press Enter
        submit_btn = args.get("submit_button")
        submit_selectors = [submit_btn] + SUBMIT_SELECTORS if submit_btn else SUBMIT_SELECTORS
        submit_sel = await _try_find_selector(page, submit_selectors, timeout=2000)
        if submit_sel:
            await page.locator(submit_sel).first.click(timeout=5000)
        else:
            await page.keyboard.press("Enter")

        # Wait for navigation
        await page.wait_for_load_state("domcontentloaded", timeout=10000)

        current_url = page.url
        current_title = await page.title()
        html = await page.content()

        # Determine login success
        redirect_away = current_url != login_url and "login" not in current_url.lower()
        has_logout = "logout" in html.lower() or "退出" in html
        has_welcome = "welcome" in html.lower() or "欢迎" in html.lower() or "dashboard" in html.lower()
        try:
            has_login_form = await page.eval_on_selector("form input[type=password]", "e => !!e") or False
        except Exception:
            has_login_form = False

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
