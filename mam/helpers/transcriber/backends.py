"""mlx-whisper (macOS, Apple Silicon) and whisper.cpp (Windows, macOS fallback)."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from mam.config import Settings, get_settings
from mam.errors import MamError
from mam.helpers.audio_optimizer.ffmpeg import binary
from mam.helpers.transcriber.base import Segment, Transcriber, TranscriptInfo

MLX_MODEL = "mlx-community/whisper-large-v3-turbo"


class MlxWhisper:
    def info(self) -> TranscriptInfo:
        from importlib.metadata import PackageNotFoundError, version

        try:
            ver = version("mlx-whisper")
        except PackageNotFoundError:
            ver = None
        return TranscriptInfo("mlx-whisper", ver, "large-v3-turbo", None, "mlx")

    def transcribe(self, audio: Path) -> list[Segment]:
        import mlx_whisper

        result = mlx_whisper.transcribe(
            str(audio),
            path_or_hf_repo=MLX_MODEL,
            condition_on_previous_text=False,
            temperature=0.0,
        )
        language = result.get("language")
        out = []
        for seg in result.get("segments", []):
            conf = None
            if seg.get("no_speech_prob") is not None:
                conf = round(1.0 - float(seg["no_speech_prob"]), 3)
            out.append(
                Segment(float(seg["start"]), float(seg["end"]), seg["text"].strip(), language, conf)
            )
        return out


class WhisperCpp:
    def __init__(self, cli: str, model: Path) -> None:
        self.cli, self.model = cli, model

    def info(self) -> TranscriptInfo:
        digest = hashlib.sha256()
        with self.model.open("rb") as fh:  # first 64 MB is enough to identify the model file
            digest.update(fh.read(64 * 1024 * 1024))
        proc = subprocess.run([self.cli, "--help"], capture_output=True, text=True)
        text = (proc.stdout + proc.stderr).lower()
        return TranscriptInfo(
            "whisper.cpp",
            None,
            self.model.stem,
            digest.hexdigest()[:16],
            "cpu" if "cpu" in text and "metal" not in text else "metal",
        )

    def transcribe(self, audio: Path) -> list[Segment]:
        with tempfile.TemporaryDirectory(prefix="mam-whisper-") as work:
            wav = Path(work) / "audio.wav"
            subprocess.run(
                [binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-i", str(audio)]
                + ["-map", "0:a:0", "-ar", "16000", "-ac", "1", str(wav)],
                check=True,
            )
            base = Path(work) / "out"
            cmd = [self.cli, "-m", str(self.model), "-f", str(wav), "-oj", "-of", str(base)]
            cmd += ["-l", "auto", "-tp", "0", "-mc", "0"]  # zero temperature, no context
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise MamError(f"whisper-cli failed: {proc.stderr.strip()[-1500:]}")
            data = json.loads(base.with_suffix(".json").read_text(encoding="utf-8"))
        language = (data.get("result") or {}).get("language")
        return [
            Segment(
                item["offsets"]["from"] / 1000,
                item["offsets"]["to"] / 1000,
                item["text"].strip(),
                language,
            )
            for item in data.get("transcription", [])
            if item["text"].strip()
        ]


def get_transcriber(settings: Settings | None = None) -> Transcriber:
    """mlx-whisper when installed (macOS), otherwise whisper.cpp with the GGML model."""
    settings = settings or get_settings()
    try:
        import mlx_whisper  # noqa: F401

        return MlxWhisper()
    except ImportError:
        pass
    cli = shutil.which("whisper-cli")
    model = settings.whisper_model_path
    if cli and model and Path(model).is_file():
        return WhisperCpp(cli, Path(model))
    raise MamError(
        "No transcription backend: run `uv sync` (installs mlx-whisper on Apple Silicon) or, "
        "on Windows, install whisper.cpp's whisper-cli and set WHISPER_MODEL_PATH to the GGML "
        "large-v3-turbo model. Check with `mam doctor`."
    )
