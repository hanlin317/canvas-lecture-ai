from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path


class WhisperUnavailable(RuntimeError):
    pass


def local_whisper_engine(command: str = "auto") -> str | None:
    requested = command.strip().lower()
    if requested in {"auto", "mlx_whisper", "mlx"} and sys.platform == "darwin":
        if importlib.util.find_spec("mlx_whisper") is not None:
            return "mlx"
        if requested != "auto" and shutil.which(command):
            return "mlx-cli"
    if (
        requested in {"auto", "faster_whisper", "faster-whisper"}
        and importlib.util.find_spec("faster_whisper") is not None
    ):
        return "faster-whisper"
    if (
        requested not in {"auto", "mlx", "faster_whisper", "faster-whisper"}
        and shutil.which(command)
    ):
        return "mlx-cli"
    return None


def local_whisper_available(command: str = "auto") -> bool:
    return local_whisper_engine(command) is not None


def mlx_whisper_available(command: str = "mlx_whisper") -> bool:
    """Backward-compatible availability check used by older integrations."""
    return local_whisper_available(command)


def _mlx_model(model: str) -> str:
    aliases = {
        "large-v3-turbo": "mlx-community/whisper-large-v3-turbo-q4",
        "medium": "mlx-community/whisper-medium-mlx",
    }
    return aliases.get(model, model)


def _faster_whisper_model(model: str) -> str:
    if "medium" in model.lower():
        return "medium"
    if model.startswith("mlx-community/"):
        return "large-v3-turbo"
    return model


def transcribe_with_local_whisper(
    video_path: Path,
    output_txt: Path,
    command: str = "auto",
    model: str = "large-v3-turbo",
    overwrite: bool = False,
) -> Path:
    if output_txt.exists() and not overwrite:
        return output_txt
    engine = local_whisper_engine(command)
    if engine is None:
        raise WhisperUnavailable(
            "Local transcription is unavailable on this installation. Official Echo360 "
            "transcripts can still be used."
        )
    output_txt.parent.mkdir(parents=True, exist_ok=True)
    if engine == "mlx":
        import mlx_whisper

        result = mlx_whisper.transcribe(str(video_path), path_or_hf_repo=_mlx_model(model))
        text = str(result.get("text") or "").strip()
    elif engine == "faster-whisper":
        import ctranslate2
        from faster_whisper import WhisperModel

        use_cuda = ctranslate2.get_cuda_device_count() > 0
        whisper = WhisperModel(
            _faster_whisper_model(model),
            device="cuda" if use_cuda else "cpu",
            compute_type="float16" if use_cuda else "int8",
        )
        segments, _ = whisper.transcribe(str(video_path), vad_filter=True)
        text = " ".join(segment.text.strip() for segment in segments if segment.text.strip())
    else:
        return _transcribe_with_cli(video_path, output_txt, command, model)
    if not text:
        raise RuntimeError("Local Whisper completed but returned an empty transcript.")
    temporary = output_txt.with_suffix(output_txt.suffix + ".part")
    temporary.write_text(text + "\n", encoding="utf-8")
    temporary.replace(output_txt)
    return output_txt


def _transcribe_with_cli(
    video_path: Path,
    output_txt: Path,
    command: str,
    model: str,
) -> Path:
    cli_command = "mlx_whisper" if command == "auto" else command
    result = subprocess.run(
        [
            cli_command,
            str(video_path),
            "--model",
            _mlx_model(model),
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
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Local Whisper failed")
    candidates = sorted(output_txt.parent.glob(f"{video_path.stem}*.txt"))
    if candidates and candidates[0] != output_txt:
        candidates[0].replace(output_txt)
    if not output_txt.exists():
        raise RuntimeError("Local Whisper completed but did not produce the expected transcript")
    return output_txt


def transcribe_with_mlx_whisper(
    video_path: Path,
    output_txt: Path,
    command: str = "mlx_whisper",
    model: str = "mlx-community/whisper-large-v3-turbo-q4",
    overwrite: bool = False,
) -> Path:
    return transcribe_with_local_whisper(
        video_path,
        output_txt,
        command=command,
        model=model,
        overwrite=overwrite,
    )
