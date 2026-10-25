from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class WhisperUnavailable(RuntimeError):
    pass


def mlx_whisper_available(command: str = "mlx_whisper") -> bool:
    return shutil.which(command) is not None


def transcribe_with_mlx_whisper(
    video_path: Path,
    output_txt: Path,
    command: str = "mlx_whisper",
    model: str = "whisper-large-v3-turbo",
    overwrite: bool = False,
) -> Path:
    if output_txt.exists() and not overwrite:
        return output_txt
    if not mlx_whisper_available(command):
        raise WhisperUnavailable(f"{command} is not installed or not on PATH.")
    output_txt.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [command, str(video_path), "--model", model, "--output-dir", str(output_txt.parent), "--output-format", "txt"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "mlx_whisper failed")
    candidates = sorted(output_txt.parent.glob(f"{video_path.stem}*.txt"))
    if candidates and candidates[0] != output_txt:
        candidates[0].replace(output_txt)
    if not output_txt.exists():
        raise RuntimeError("mlx_whisper completed but did not produce expected transcript")
    return output_txt
