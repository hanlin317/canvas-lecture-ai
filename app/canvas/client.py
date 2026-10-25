from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlparse

import httpx
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from app.canvas.models import CanvasCourseSummary, CanvasModule, CanvasModuleItem
from app.config import Settings

logger = logging.getLogger(__name__)


class CanvasAuthError(RuntimeError):
    pass


class CanvasClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.canvas.base_url.rstrip("/")

    @property
    def state_path(self) -> Path:
        return self.settings.canvas_state_path

    def has_session(self) -> bool:
        return self.state_path.exists()

    def _is_authentication_url(self, value: str) -> bool:
        current = urlparse(value)
        canvas = urlparse(self.base_url)
        if current.hostname != canvas.hostname:
            return True
        return bool(re.search(r"/(login|sso|oauth|authorize)(?:/|$)", current.path, re.IGNORECASE))

    def _is_authenticated_page(self, url: str, body_text: str) -> bool:
        if self._is_authentication_url(url):
            return False
        return bool(re.search(r"\b(dashboard|courses|account|inbox)\b", body_text, re.IGNORECASE))

    async def _wait_for_authenticated_canvas(self, page) -> None:
        deadline = asyncio.get_running_loop().time() + self.settings.canvas.login_timeout_seconds
        consecutive_matches = 0
        while asyncio.get_running_loop().time() < deadline:
            if page.is_closed():
                raise CanvasAuthError("Canvas login window was closed before authentication completed.")
            try:
                body_text = await page.locator("body").inner_text(timeout=2_000)
                authenticated = self._is_authenticated_page(page.url, body_text)
            except PlaywrightError:
                authenticated = False
            consecutive_matches = consecutive_matches + 1 if authenticated else 0
            if consecutive_matches >= 2:
                return
            await page.wait_for_timeout(1_000)
        raise CanvasAuthError("Canvas login timed out. Complete university login and MFA, then try again.")

    async def login(self) -> None:
        if not self.base_url:
            raise CanvasAuthError("Set canvas.base_url in config.toml before logging in.")
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        profile_dir = Path(self.settings.canvas.login_profile_dir).expanduser()
        profile_dir.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as p:
            channel = self.settings.canvas.login_browser_channel.strip() or None
            try:
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    channel=channel,
                    headless=False,
                )
            except PlaywrightError as exc:
                if not channel:
                    raise
                logger.warning("Could not launch %s; falling back to bundled Chromium: %s", channel, exc)
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    headless=False,
                )
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                await page.goto(self.base_url, wait_until="domcontentloaded")
                logger.info("Waiting for Canvas authentication in the visible browser.")
                await self._wait_for_authenticated_canvas(page)
                await context.storage_state(path=str(self.state_path))
                os.chmod(self.state_path, 0o600)
            finally:
                await context.close()

    async def session_is_valid(self) -> bool:
        if not self.has_session() or not self.base_url:
            return False
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(storage_state=str(self.state_path))
                page = await context.new_page()
                await page.goto(urljoin(self.base_url + "/", "courses"), wait_until="domcontentloaded")
                await page.wait_for_timeout(1_500)
                current = page.url
                text = await page.locator("body").inner_text(timeout=5000)
                await browser.close()
                return self._is_authenticated_page(current, text)
        except Exception as exc:  # noqa: BLE001
            logger.info("Canvas session check failed: %s", exc)
            return False

    async def _api_get(self, path: str, params: dict[str, str] | None = None) -> list[dict]:
        token = os.environ.get("CANVAS_ACCESS_TOKEN")
        if not token:
            raise CanvasAuthError("CANVAS_ACCESS_TOKEN not set; use browser diagnostics/fallback.")
        async with httpx.AsyncClient(base_url=self.base_url, timeout=30) as client:
            response = await client.get(
                path,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def _api_get_with_browser_session(
        self,
        path: str,
        params: dict[str, str | list[str]] | None = None,
    ) -> list[dict]:
        if not self.has_session():
            raise CanvasAuthError(
                "No Canvas API token or saved browser session found. Run `python -m app login` first, "
                "or set CANVAS_ACCESS_TOKEN."
            )
        if not self.base_url:
            raise CanvasAuthError("Set canvas.base_url in config.toml.")

        query = f"?{urlencode(params or {}, doseq=True)}" if params else ""
        api_url = urljoin(self.base_url + "/", path.lstrip("/")) + query
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(storage_state=str(self.state_path))
            response = await context.request.get(api_url)
            result = {"ok": response.ok, "status": response.status, "text": await response.text()}
            await browser.close()
        if not result["ok"]:
            raise CanvasAuthError(f"Canvas browser-session API request failed with HTTP {result['status']}.")
        try:
            data = json.loads(result["text"])
        except json.JSONDecodeError as exc:
            raise CanvasAuthError("Canvas returned a non-JSON API response; saved session may be expired.") from exc
        if not isinstance(data, list):
            raise CanvasAuthError("Canvas API response was not the expected list shape.")
        return data

    async def list_courses(self, active_only: bool = True) -> list[CanvasCourseSummary]:
        if not os.environ.get("CANVAS_ACCESS_TOKEN"):
            return await self.list_courses_from_browser(active_only=active_only)
        params = {"enrollment_state": "active"} if active_only else {}
        data = await self._api_get("/api/v1/courses", params=params)
        courses = []
        for item in data:
            name = item.get("name") or item.get("course_code") or f"Course {item.get('id')}"
            code = item.get("course_code") or name.split(" ")[0]
            courses.append(
                CanvasCourseSummary(
                    canvas_id=str(item["id"]),
                    code=code,
                    name=name,
                    url=f"{self.base_url}/courses/{item['id']}",
                )
            )
        return courses

    async def list_courses_from_browser(self, active_only: bool = True) -> list[CanvasCourseSummary]:
        if not self.has_session():
            raise CanvasAuthError(
                "No Canvas API token or saved browser session found. Run `python -m app login` first, "
                "or set CANVAS_ACCESS_TOKEN."
            )
        if not self.base_url:
            raise CanvasAuthError("Set canvas.base_url in config.toml before syncing courses.")

        try:
            params: dict[str, str | list[str]] = {"per_page": "100"}
            if active_only:
                params["enrollment_state"] = "active"
            data = await self._api_get_with_browser_session("/api/v1/courses", params)
            courses = []
            for item in data:
                name = item.get("name") or item.get("course_code") or f"Course {item.get('id')}"
                code = item.get("course_code") or name.split(" ")[0]
                courses.append(
                    CanvasCourseSummary(
                        canvas_id=str(item["id"]),
                        code=code,
                        name=name,
                        url=f"{self.base_url}/courses/{item['id']}",
                    )
                )
            if courses:
                return sorted(courses, key=lambda item: (item.code, item.name))
        except CanvasAuthError as exc:
            logger.info("Canvas browser API course lookup failed, falling back to page scrape: %s", exc)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(storage_state=str(self.state_path))
            page = await context.new_page()
            await page.goto(urljoin(self.base_url + "/", "courses"), wait_until="networkidle")
            current = page.url
            text = await page.locator("body").inner_text(timeout=5_000)
            if not self._is_authenticated_page(current, text):
                await browser.close()
                raise CanvasAuthError("Saved Canvas session appears expired. Run `python -m app login` again.")
            links = await page.eval_on_selector_all(
                "a[href*='/courses/']",
                r"""
                els => els.map(a => ({
                    text: (a.innerText || a.textContent || '').trim(),
                    href: a.href
                })).filter(x => x.text && /\\/courses\\/\\d+/.test(x.href))
                """,
            )
            await browser.close()

        courses: dict[str, CanvasCourseSummary] = {}
        for link in links:
            match = re.search(r"/courses/(\d+)", link["href"])
            if not match:
                continue
            canvas_id = match.group(1)
            text = re.sub(r"\s+", " ", link["text"]).strip()
            if not text or text.lower() in {"courses", "all courses"}:
                continue
            code = text.split(" ")[0].strip(":-")
            courses[canvas_id] = CanvasCourseSummary(
                canvas_id=canvas_id,
                code=code,
                name=text,
                url=f"{self.base_url}/courses/{canvas_id}",
            )
        if not courses:
            raise CanvasAuthError(
                "Could not discover courses from the Canvas courses page. Try setting CANVAS_ACCESS_TOKEN, "
                "or run `python -m app diagnose-canvas` and inspect the captured links."
            )
        return sorted(courses.values(), key=lambda item: (item.code, item.name))

    async def list_modules(self, canvas_course_id: str) -> list[CanvasModule]:
        if not os.environ.get("CANVAS_ACCESS_TOKEN"):
            return await self.list_modules_from_browser(canvas_course_id)
        modules = await self._api_get(f"/api/v1/courses/{canvas_course_id}/modules", {"per_page": "100"})
        result = []
        for module in modules:
            raw_items = await self._api_get(
                f"/api/v1/courses/{canvas_course_id}/modules/{module['id']}/items",
                {"per_page": "100"},
            )
            items = [
                CanvasModuleItem(
                    id=str(item.get("id")),
                    title=item.get("title", ""),
                    type=item.get("type", ""),
                    url=item.get("url"),
                    html_url=item.get("html_url"),
                    external_url=item.get("external_url"),
                )
                for item in raw_items
            ]
            result.append(CanvasModule(id=str(module["id"]), name=module.get("name", ""), items=items))
        return result

    async def list_modules_from_browser(self, canvas_course_id: str) -> list[CanvasModule]:
        if not self.has_session():
            raise CanvasAuthError(
                "No Canvas API token or saved browser session found. Run `python -m app login` first, "
                "or set CANVAS_ACCESS_TOKEN."
            )
        if not self.base_url:
            raise CanvasAuthError("Set canvas.base_url in config.toml before discovering course content.")

        try:
            modules = await self._api_get_with_browser_session(
                f"/api/v1/courses/{canvas_course_id}/modules",
                {"per_page": "100", "include[]": ["items"]},
            )
            result = []
            for module in modules:
                raw_items = module.get("items")
                if raw_items is None:
                    raw_items = await self._api_get_with_browser_session(
                        f"/api/v1/courses/{canvas_course_id}/modules/{module['id']}/items",
                        {"per_page": "100"},
                    )
                items = [
                    CanvasModuleItem(
                        id=str(item.get("id")),
                        title=item.get("title", ""),
                        type=item.get("type", ""),
                        url=item.get("url"),
                        html_url=item.get("html_url"),
                        external_url=item.get("external_url"),
                    )
                    for item in raw_items
                ]
                result.append(CanvasModule(id=str(module["id"]), name=module.get("name", ""), items=items))
            if result:
                return result
        except CanvasAuthError as exc:
            logger.info("Canvas browser API module lookup failed, falling back to course page scrape: %s", exc)

        course_url = urljoin(self.base_url + "/", f"courses/{canvas_course_id}")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(storage_state=str(self.state_path))
            page = await context.new_page()
            await page.goto(course_url, wait_until="networkidle")
            current = page.url
            text = await page.locator("body").inner_text(timeout=5_000)
            if not self._is_authenticated_page(current, text):
                await browser.close()
                raise CanvasAuthError("Saved Canvas session appears expired. Run `python -m app login` again.")
            links = await page.eval_on_selector_all(
                "a",
                """
                els => els.map(a => ({
                    text: (a.innerText || a.textContent || '').trim(),
                    href: a.href
                })).filter(x => x.href)
                """,
            )
            await browser.close()

        items = [
            CanvasModuleItem(
                id=f"browser-{index}",
                title=(link.get("text") or link.get("href") or "Canvas link"),
                type="ExternalUrl",
                html_url=link.get("href"),
                external_url=link.get("href"),
            )
            for index, link in enumerate(links, start=1)
        ]
        return [CanvasModule(id="browser-course-page", name="Canvas course page links", items=items)]

    @staticmethod
    def echo360_links_from_modules(modules: list[CanvasModule]) -> list[str]:
        links: list[str] = []
        for module in modules:
            for item in module.items:
                candidates = [item.url, item.html_url, item.external_url]
                for url in candidates:
                    if url and re.search(r"echo360|echo", url, re.IGNORECASE):
                        links.append(url)
        return sorted(set(links))

    async def echo360_navigation_links(self, canvas_course_id: str) -> list[str]:
        """Find recording tools configured in the Canvas course navigation."""
        try:
            if os.environ.get("CANVAS_ACCESS_TOKEN"):
                tabs = await self._api_get(f"/api/v1/courses/{canvas_course_id}/tabs", {"per_page": "100"})
            else:
                tabs = await self._api_get_with_browser_session(
                    f"/api/v1/courses/{canvas_course_id}/tabs",
                    {"per_page": "100"},
                )
        except CanvasAuthError as exc:
            logger.info("Canvas course navigation API lookup failed: %s", exc)
            tabs = []

        links: list[str] = []
        for tab in tabs:
            label = str(tab.get("label") or tab.get("id") or "")
            url = tab.get("html_url") or tab.get("url")
            is_recording_tool = re.search(r"echo|recorded?\s+lectures?|lecture\s+recordings?", label, re.IGNORECASE)
            is_echo_url = url and re.search(r"echo360|echo", str(url), re.IGNORECASE)
            if url and (is_recording_tool or is_echo_url):
                links.append(urljoin(self.base_url + "/", str(url)))

        if links:
            return list(dict.fromkeys(links))

        course_url = urljoin(self.base_url + "/", f"courses/{canvas_course_id}")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(storage_state=str(self.state_path))
            page = await context.new_page()
            await page.goto(course_url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(1_500)
            body_text = await page.locator("body").inner_text(timeout=5_000)
            if not self._is_authenticated_page(page.url, body_text):
                await browser.close()
                raise CanvasAuthError("Saved Canvas session expired. Click Login to Canvas and sign in again.")
            nav_links = []
            for attempt in range(3):
                await page.wait_for_timeout(1_000)
                try:
                    nav_links = await page.eval_on_selector_all(
                        "a",
                        r"""
                        els => els.map(a => ({
                            text: (a.innerText || a.textContent || '').trim(),
                            href: a.href
                        })).filter(x => x.href && /echo|recorded?\s+lectures?|lecture\s+recordings?/i.test(x.text + ' ' + x.href))
                        """,
                    )
                    break
                except PlaywrightError as exc:
                    if "Execution context was destroyed" not in str(exc) or attempt == 2:
                        raise
            await browser.close()
        return list(dict.fromkeys(str(link["href"]) for link in nav_links))

    async def capture_browser_diagnostics(self, output_path: Path) -> Path:
        """Capture low-sensitivity page/link data from an authenticated Canvas session."""
        if not self.has_session():
            raise CanvasAuthError("No Playwright state found; run login first.")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context(storage_state=str(self.state_path))
            page = await context.new_page()
            await page.goto(self.base_url, wait_until="networkidle")
            links = await page.eval_on_selector_all(
                "a",
                "els => els.map(a => ({text: a.innerText, href: a.href})).filter(x => x.href)",
            )
            await browser.close()
        output_path.write_text(json.dumps({"base_url": self.base_url, "links": links}, indent=2), encoding="utf-8")
        return output_path
