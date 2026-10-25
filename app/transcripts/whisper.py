from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path


class WhisperUnavailable(RuntimeError):
    pass


def mlx_whisper_available(command: str = "mlx_whisper") -> bool:
    return importlib.util.find_spec("mlx_whisper") is not None or shutil.which(command) is not None


def transcribe_with_mlx_whisper(
    video_path: Path,
    output_txt: Path,
    command: str = "mlx_whisper",
    model: str = "mlx-community/whisper-large-v3-turbo-q4",
    overwrite: bool = False,
) -> Path:
    if output_txt.exists() and not overwrite:
        return output_txt
    if not mlx_whisper_available(command):
        raise WhisperUnavailable(f"{command} is not installed or not on PATH.")
    output_txt.parent.mkdir(parents=True, exist_ok=True)
    if importlib.util.find_spec("mlx_whisper") is not None:
        import mlx_whisper

        result = mlx_whisper.transcribe(str(video_path), path_or_hf_repo=model)
        text = str(result.get("text") or "").strip()
        if not text:
            raise RuntimeError("Local Whisper completed but returned an empty transcript.")
        temporary = output_txt.with_suffix(output_txt.suffix + ".part")
        temporary.write_text(text + "\n", encoding="utf-8")
        temporary.replace(output_txt)
        return output_txt
    result = subprocess.run(
        [
            command,
            str(video_path),
            "--model",
            model,
            "--output-dir",
            str(output_txt.parent),
            "--output-format",
            "txt",
        ],
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
