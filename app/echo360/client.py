from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from playwright.async_api import Request, Response, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.config import Settings
from app.models import EchoPresentationAsset, EchoRecording, EchoTranscriptAsset, EchoVideoAsset

logger = logging.getLogger(__name__)


@dataclass
class ResolvedEchoAssets:
    video_url: str | None = None
    audio_url: str | None = None
    transcript_url: str | None = None
    slides_url: str | None = None
    slides_extension: str | None = None
    headers_by_url: dict[str, dict[str, str]] = field(default_factory=dict)


class Echo360Client:
    """Echo360 adapter.

    Echo360 deployments vary. This class keeps browser-visible API inspection in one
    place and stores diagnostics that can be used to finalize site-specific parsing.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def discover_course(self, launch_url: str, diagnostic_output: Path | None = None) -> list[EchoRecording]:
        payload = await self.inspect_launch(launch_url, diagnostic_output=diagnostic_output)
        return self.parse_browser_payload(payload)

    async def inspect_launch(self, launch_url: str, diagnostic_output: Path | None = None) -> dict:
        state_path = self.settings.canvas_state_path
        if not state_path.exists():
            raise RuntimeError("Canvas/Echo360 Playwright state does not exist. Run login first.")

        requests: list[dict] = []
        responses: list[dict] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(storage_state=str(state_path))
            page = await context.new_page()

            async def on_request(request: Request) -> None:
                if re.search(r"echo360|echo", request.url, re.IGNORECASE):
                    requests.append({"method": request.method, "url": request.url})

            async def on_response(response: Response) -> None:
                if not re.search(r"echo360|echo", response.url, re.IGNORECASE):
                    return
                entry = {"status": response.status, "url": response.url, "content_type": response.headers.get("content-type")}
                try:
                    if "json" in (entry["content_type"] or ""):
                        body = await response.json()
                        entry["json_keys"] = list(body.keys()) if isinstance(body, dict) else type(body).__name__
                        entry["sample"] = body
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Could not parse Echo360 response body for diagnostics: %s", exc)
                responses.append(entry)

            context.on("request", on_request)
            context.on("response", on_response)
            try:
                await page.goto(launch_url, wait_until="domcontentloaded", timeout=30_000)
            except PlaywrightTimeoutError:
                logger.info("Echo360 launch kept loading after 30 seconds; inspecting the loaded page state.")
            await page.wait_for_timeout(15_000)
            title = await page.title()
            links: list[dict] = []
            frame_urls: list[str] = []
            for frame in page.frames:
                frame_urls.append(frame.url)
                try:
                    links.extend(
                        await frame.eval_on_selector_all(
                            "a",
                            "els => els.map(a => ({text: a.innerText, href: a.href})).filter(x => x.href)",
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Could not inspect links in frame %s: %s", frame.url, exc)
            await browser.close()

        payload = {
            "launch_url": launch_url,
            "title": title,
            "frame_urls": frame_urls,
            "requests": requests,
            "responses": responses,
            "links": links,
        }
        if diagnostic_output:
            diagnostic_output.parent.mkdir(parents=True, exist_ok=True)
            diagnostic_output.write_text(json.dumps(_redact_diagnostic_value(payload), indent=2), encoding="utf-8")
        return payload

    async def resolve_recording_assets(
        self,
        launch_url: str,
        recording_url: str,
        presentation_id: str | None = None,
    ) -> ResolvedEchoAssets:
        """Resolve permission-protected media URLs for one authenticated lesson."""
        state_path = self.settings.canvas_state_path
        if not state_path.exists():
            raise RuntimeError("Canvas/Echo360 Playwright state does not exist. Run login first.")

        tracks: list[tuple[int, int, str]] = []
        transcript_urls: list[str] = []
        media_ids: list[str] = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(storage_state=str(state_path))
            page = await context.new_page()

            async def on_response(response: Response) -> None:
                url = response.url
                path = urlsplit(url).path
                track_match = re.search(r"/s([01])q(\d+)\.mp4$", path, re.IGNORECASE)
                if response.status in {200, 206} and track_match and "content.echo360" in url:
                    tracks.append((int(track_match.group(1)), int(track_match.group(2)), url))
                if response.status == 200 and path.lower().endswith(".vtt") and "captions.echo360" in url:
                    transcript_urls.append(url)
                media_match = re.search(r"/medias/([^/]+)/transcript", path, re.IGNORECASE)
                if media_match:
                    media_ids.append(media_match.group(1))

            context.on("response", on_response)
            try:
                await page.goto(launch_url, wait_until="domcontentloaded", timeout=30_000)
            except PlaywrightTimeoutError:
                logger.info("Echo360 course launch kept loading; continuing with available session state.")
            await self._wait_for_echo_frame(page)
            try:
                await page.goto(recording_url, wait_until="domcontentloaded", timeout=30_000)
            except PlaywrightTimeoutError:
                logger.info("Echo360 lesson kept loading; resolving media from captured requests.")
            await page.wait_for_timeout(10_000)

            video_url = _best_track_url(tracks, stream=1)
            audio_url = _best_track_url(tracks, stream=0)
            transcript_url = _matching_transcript_url(transcript_urls, media_ids)
            slides_url = None
            slides_extension = None
            if presentation_id:
                recording_parts = urlsplit(recording_url)
                origin = f"{recording_parts.scheme}://{recording_parts.netloc}"
                lesson_match = re.search(r"/lesson/([^/]+)", recording_parts.path)
                lesson_id = lesson_match.group(1) if lesson_match else ""
                info_url = f"{origin}/api/ui/library/medias/{quote(presentation_id, safe='')}/download-info"
                info_response = await context.request.get(info_url)
                if info_response.status == 200:
                    info = await info_response.json()
                    data = info.get("data") if isinstance(info, dict) else None
                    presentation_file = data.get("presentationFile") if isinstance(data, dict) else None
                    filename = presentation_file.get("fileName") if isinstance(presentation_file, dict) else None
                    if filename:
                        slides_extension = Path(str(filename)).suffix.lower().lstrip(".") or "pdf"
                        slides_url = (
                            f"{origin}/media/download/{quote(presentation_id, safe='')}/"
                            f"{quote(str(filename), safe='')}?lessonId={quote(lesson_id, safe='')}"
                        )
            urls = [url for url in (video_url, audio_url, transcript_url, slides_url) if url]
            headers_by_url: dict[str, dict[str, str]] = {}
            for url in urls:
                cookies = await context.cookies(url)
                if cookies:
                    headers_by_url[url] = {"Cookie": "; ".join(f"{cookie['name']}={cookie['value']}" for cookie in cookies)}
            await browser.close()
        return ResolvedEchoAssets(
            video_url=video_url,
            audio_url=audio_url,
            transcript_url=transcript_url,
            slides_url=slides_url,
            slides_extension=slides_extension,
            headers_by_url=headers_by_url,
        )

    @staticmethod
    async def _wait_for_echo_frame(page, timeout_seconds: int = 15) -> None:
        for _ in range(timeout_seconds * 2):
            if any("echo360" in frame.url for frame in page.frames):
                return
            await page.wait_for_timeout(500)

    def parse_browser_payload(self, payload: dict) -> list[EchoRecording]:
        recordings: dict[str, EchoRecording] = {}
        for response in payload.get("responses", []):
            sample = response.get("sample")
            syllabus_recordings = self._parse_syllabus(sample, response.get("url"))
            if syllabus_recordings:
                recordings.update({recording.source_id: recording for recording in syllabus_recordings})
                continue
            if isinstance(sample, (dict, list)):
                self._scan_json(sample, recordings, response.get("url"))
        return sorted(recordings.values(), key=lambda item: (item.order, item.date or "", item.title))

    @staticmethod
    def _parse_syllabus(sample: object, source_url: str | None) -> list[EchoRecording]:
        if not isinstance(sample, dict) or not isinstance(sample.get("data"), list):
            return []
        groups = sample["data"]
        if not any(isinstance(group, dict) and "groupInfo" in group and "lessons" in group for group in groups):
            return []

        origin = ""
        if source_url:
            parts = urlsplit(source_url)
            origin = f"{parts.scheme}://{parts.netloc}"

        recordings: list[EchoRecording] = []
        order = 0
        for group in groups:
            if not isinstance(group, dict):
                continue
            group_info = group.get("groupInfo") if isinstance(group.get("groupInfo"), dict) else {}
            topic = str(group_info.get("name") or "Lectures").strip()
            for wrapper in group.get("lessons") or []:
                lesson_container = wrapper.get("lesson") if isinstance(wrapper, dict) else None
                if not isinstance(lesson_container, dict):
                    continue
                lesson = lesson_container.get("lesson")
                if not isinstance(lesson, dict) or not lesson.get("id"):
                    continue
                order += 1
                lesson_id = str(lesson["id"])
                title = str(lesson.get("displayName") or lesson.get("name") or f"Lecture {order}")
                timing = lesson.get("timing") if isinstance(lesson.get("timing"), dict) else {}
                date = timing.get("start") or lesson_container.get("startTimeUTC")
                recording_url = f"{origin}/lesson/{lesson_id}/classroom" if origin else str(source_url or "")
                recordings.append(
                    EchoRecording(
                        source_id=lesson_id,
                        title=title,
                        url=recording_url,
                        date=str(date) if date else None,
                        order=order,
                        topic_hint=topic,
                        presentations=[
                            EchoPresentationAsset(
                                media_id=str(media["id"]),
                                filename=str(media.get("title") or "slides.pdf"),
                            )
                            for media in (lesson_container.get("medias") or [])
                            if isinstance(media, dict)
                            and str(media.get("mediaType", "")).lower() == "presentation"
                            and media.get("id")
                            and media.get("isAvailable", True)
                        ],
                    )
                )
        return recordings

    def _scan_json(self, value: object, recordings: dict[str, EchoRecording], source_url: str | None) -> None:
        if isinstance(value, list):
            for item in value:
                self._scan_json(item, recordings, source_url)
            return
        if not isinstance(value, dict):
            return

        title = value.get("title") or value.get("name") or value.get("displayName")
        identifier = value.get("id") or value.get("lessonId") or value.get("mediaId")
        url = value.get("url") or value.get("playbackUrl") or source_url
        if title and identifier and url:
            key = str(identifier)
            recording = recordings.setdefault(
                key,
                EchoRecording(
                    source_id=key,
                    title=str(title),
                    url=str(url),
                    date=value.get("date") or value.get("startTime") or value.get("createdAt"),
                    duration_seconds=value.get("duration") if isinstance(value.get("duration"), int) else None,
                ),
            )
            for candidate in ("videoUrl", "downloadUrl", "mediaUrl", "playbackUrl"):
                if value.get(candidate):
                    recording.videos.append(EchoVideoAsset(url=str(value[candidate]), quality=str(value.get("quality") or "unknown")))
            for candidate in ("transcriptUrl", "vttUrl", "captionUrl"):
                if value.get(candidate):
                    fmt = "vtt" if str(value[candidate]).lower().endswith(".vtt") else "txt"
                    recording.transcripts.append(EchoTranscriptAsset(url=str(value[candidate]), format=fmt))

        for child in value.values():
            self._scan_json(child, recordings, source_url)

    async def list_recordings(self, launch_url: str) -> list[EchoRecording]:
        return await self.discover_course(launch_url)

    async def get_recording(self, launch_url: str, recording_id: str) -> EchoRecording | None:
        for recording in await self.list_recordings(launch_url):
            if recording.source_id == recording_id:
                return recording
        return None


def _redact_diagnostic_value(value: object) -> object:
    if isinstance(value, dict):
        return {key: _redact_diagnostic_value(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_redact_diagnostic_value(child) for child in value]
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        parts = urlsplit(value)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return value


def _best_track_url(tracks: list[tuple[int, int, str]], stream: int) -> str | None:
    candidates = {(quality, url) for track_stream, quality, url in tracks if track_stream == stream}
    return max(candidates, default=(0, None), key=lambda item: item[0])[1]


def _matching_transcript_url(urls: list[str], media_ids: list[str]) -> str | None:
    for media_id in reversed(media_ids):
        for url in reversed(urls):
            if media_id in url:
                return url
    return urls[-1] if urls else None
