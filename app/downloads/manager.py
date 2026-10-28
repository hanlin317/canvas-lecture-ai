from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


def ffmpeg_executable() -> str:
    configured = os.environ.get("DLM_FFMPEG")
    if configured and Path(configured).is_file():
        return configured
    system = shutil.which("ffmpeg")
    if system:
        return system
    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        return get_ffmpeg_exe()
    except (ImportError, RuntimeError) as exc:
        raise RuntimeError(
            "FFmpeg is unavailable. Install FFmpeg or reinstall Canvas Lecture AI."
        ) from exc


class DownloadManager:
    def __init__(self, concurrency: int = 3, retries: int = 2) -> None:
        self.semaphore = asyncio.Semaphore(concurrency)
        self.retries = retries

    async def download(
        self,
        url: str,
        destination: Path,
        overwrite: bool = False,
        headers: dict[str, str] | None = None,
    ) -> str:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and not overwrite:
            return "skipped"
        tmp = destination.with_suffix(destination.suffix + ".part")
        async with self.semaphore:
            last_error: Exception | None = None
            for attempt in range(self.retries + 1):
                try:
                    partial_size = tmp.stat().st_size if tmp.exists() else 0
                    request_headers = dict(headers or {})
                    if partial_size:
                        request_headers["Range"] = f"bytes={partial_size}-"
                    async with (
                        httpx.AsyncClient(follow_redirects=True, timeout=None) as client,
                        client.stream("GET", url, headers=request_headers) as response,
                    ):
                        response.raise_for_status()
                        resumed = partial_size > 0 and response.status_code == 206
                        mode = "ab" if resumed else "wb"
                        with tmp.open(mode) as handle:
                            async for chunk in response.aiter_bytes(1024 * 1024):
                                if chunk:
                                    handle.write(chunk)
                    tmp.replace(destination)
                    return "complete"
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    logger.warning("Download failed attempt %s for %s: %s", attempt + 1, url, exc)
                    await asyncio.sleep(1 + attempt)
            raise RuntimeError(f"download failed for {url}: {last_error}")

    async def download_and_mux(
        self,
        video_url: str,
        audio_url: str,
        destination: Path,
        *,
        video_headers: dict[str, str] | None = None,
        audio_headers: dict[str, str] | None = None,
        overwrite: bool = False,
    ) -> str:
        if destination.exists() and not overwrite:
            return "skipped"
        destination.parent.mkdir(parents=True, exist_ok=True)
        stem = destination.with_suffix("")
        video_part = stem.with_name(stem.name + ".video-track.mp4")
        audio_part = stem.with_name(stem.name + ".audio-track.mp4")
        output_part = destination.with_suffix(destination.suffix + ".part")
        try:
            await asyncio.gather(
                self.download(video_url, video_part, overwrite=True, headers=video_headers),
                self.download(audio_url, audio_part, overwrite=True, headers=audio_headers),
            )
            process = await asyncio.create_subprocess_exec(
                ffmpeg_executable(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(video_part),
                "-i",
                str(audio_part),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                "-f",
                "mp4",
                str(output_part),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await process.communicate()
            if process.returncode != 0:
                raise RuntimeError(f"ffmpeg failed: {stderr.decode(errors='replace').strip()}")
            output_part.replace(destination)
            return "complete"
        finally:
            for path in (video_part, audio_part, output_part):
                if path.exists():
                    path.unlink()
