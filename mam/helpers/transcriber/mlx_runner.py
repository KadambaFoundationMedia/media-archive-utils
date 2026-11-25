"""mlx-whisper in a watched subprocess, one chunk at a time.

Why: a GPU decode on a long recording can stall for good (seen on a 75 minute class: 17 minutes
at 15% CPU, no error). Inside the same process that cannot be stopped, so the model runs in a
child process that loads the audio once and answers chunk requests. The parent waits for each
chunk with a time limit and kills and restarts the child when one stalls, so a stall costs one
chunk, not the whole recording.
"""

from __future__ import annotations

import contextlib
import logging
import multiprocessing as mp
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mam.errors import MamError
from mam.helpers.progress import check_cancelled, kill_on_stop, report
from mam.helpers.transcriber.chunking import SAMPLE_RATE, pick_boundaries

log = logging.getLogger(__name__)

# Attempt 1 is the plain greedy decode. Attempt 2 lets Whisper retry a window that looped or
# scored badly at higher temperatures (its own compression-ratio / logprob checks).
ATTEMPT_OPTIONS: list[dict[str, Any]] = [
    {"temperature": 0.0},
    {
        "temperature": (0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
        "compression_ratio_threshold": 2.4,
        "logprob_threshold": -1.0,
        "no_speech_threshold": 0.6,
    },
]


# A window Whisper skipped as 'no speech' although someone read (seen on the Oslo class: the
# translation of the verse, 30 s, missing) is decoded again with those checks switched off.
REPAIR_OPTIONS: dict[str, Any] = {
    "temperature": 0.0,
    "no_speech_threshold": None,
    "logprob_threshold": None,
    "compression_ratio_threshold": None,
}
REPAIR_MIN_GAP_S = 20.0
REPAIR_MAX_GAP_S = 90.0  # a longer silence is real (a pause, applause)
REPAIR_UNTIL_S = 900.0  # the verse is read at the start of the class
REPAIR_MAX_GAPS = 8


# Speech only (Settings): a chunk with (almost) no speech is not transcribed at all, and text that
# Whisper wrote over stretches without speech (kirtan, silence, room noise) is dropped.
MIN_SPEECH_SHARE = 0.02  # share of the seconds of a chunk that must hold speech
SPEECH_P = 0.10  # a second counts as speech above this voice probability (kirtan 0.00-0.01)
NON_SPEECH_P = 0.05  # text over seconds averaging below this is dropped


def speech_profile(part) -> list[float]:
    """Voice probability per second of a 16 kHz mono chunk (the Kirtan splitter's Silero model)."""
    import numpy as np

    from mam.tools.kirtan_splitter.features import profile_from_samples

    return [p or 0.0 for p in profile_from_samples(np.asarray(part, dtype="float32"))]


def speech_share(profile: list[float]) -> float:
    return sum(1 for p in profile if p >= SPEECH_P) / len(profile) if profile else 0.0


def drop_non_speech(segments: list[dict], profile: list[float]) -> tuple[list[dict], int]:
    """Remove segments whose seconds hold no speech; returns (kept, how many were dropped)."""
    kept, dropped = [], 0
    for seg in segments:
        first = int(seg["start"] or 0)
        last = max(first + 1, int((seg["end"] or 0) + 0.999))
        window = profile[first:last]
        if window and sum(window) / len(window) < NON_SPEECH_P:
            dropped += 1
        else:
            kept.append(seg)
    return kept, dropped


class ChunkStalled(MamError):
    """A chunk did not finish within its time limit."""


def _slim(result: dict[str, Any]) -> dict[str, Any]:
    keep = ("start", "end", "text", "avg_logprob", "words", "no_speech_prob")
    return {
        "language": result.get("language"),
        "segments": [{k: s.get(k) for k in keep} for s in result.get("segments", [])],
    }


def _worker(
    conn,
    audio_path: str,
    repo: str,
    language: str | None,
    words: bool,
    chunk_s: float,
    prompt: str | None = None,
    speech_only: bool = False,
):
    """Child process: load the audio and the model once, then answer chunk requests."""
    try:
        import mlx_whisper
        from mlx_whisper.audio import load_audio

        audio = load_audio(audio_path)
        conn.send(("ready", pick_boundaries(audio, chunk_s)))
        while True:
            job = conn.recv()
            if job is None:
                return
            idx, start, end, options = job
            options = dict(options)
            lang = options.pop("language", language)
            if prompt:
                options["initial_prompt"] = prompt
            part = audio[int(start * SAMPLE_RATE) : int(end * SAMPLE_RATE)]
            profile = speech_profile(part) if speech_only and idx < 1000 else None
            if profile is not None and speech_share(profile) < MIN_SPEECH_SHARE:
                conn.send(
                    ("done", idx, {"language": None, "segments": [], "vad": {"skipped": True}})
                )
                continue
            result = mlx_whisper.transcribe(
                part,
                path_or_hf_repo=repo,
                language=lang,
                condition_on_previous_text=False,  # no carried context: no runaway repetition
                word_timestamps=words,
                verbose=None,
                **options,
            )
            slim = _slim(result)
            if profile is not None:
                slim["segments"], dropped = drop_non_speech(slim["segments"], profile)
                slim["vad"] = {"skipped": False, "dropped": dropped}
            conn.send(("done", idx, slim))
    except BaseException as exc:  # reported to the parent, which decides what to do
        with contextlib.suppress(Exception):  # the pipe is gone: the parent notices the exit
            conn.send(("error", repr(exc)))


class MlxRunner:
    """The parent side: starts the child, asks for chunks, enforces the time limit."""

    def __init__(
        self,
        audio: Path,
        repo: str,
        language: str | None,
        words: bool,
        chunk_s: float,
        prompt: str | None = None,
        speech_only: bool = False,
    ):
        self.args = (str(audio), repo, language, words, chunk_s, prompt, speech_only)
        self.proc: mp.Process | None = None
        self.conn = None
        self.boundaries: list[float] = []

    def start(self, timeout: float) -> None:
        self.stop()
        ctx = mp.get_context("spawn")  # a clean process; forking a Metal process is unsafe
        parent, child = ctx.Pipe()
        self.proc = ctx.Process(target=_worker, args=(child, *self.args), daemon=True)
        self.proc.start()
        proc = self.proc
        kill_on_stop(proc.kill)  # the Stop button ends the transcription at once
        child.close()
        self.conn = parent
        message = self._wait(timeout, "loading the audio and the model")
        if message[0] != "ready":
            raise MamError(f"mlx-whisper could not start: {message[-1]}")
        self.boundaries = message[1]

    def _wait(self, timeout: float, what: str):
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                self.stop()
                raise ChunkStalled(f"mlx-whisper made no progress in {timeout:.0f} s ({what})")
            if self.conn.poll(min(left, 2.0)):
                try:
                    return self.conn.recv()
                except EOFError:
                    break
            if self.proc is not None and not self.proc.is_alive():
                break
        code = self.proc.exitcode if self.proc else None
        self.stop()
        raise MamError(f"mlx-whisper stopped unexpectedly (exit code {code}) while {what}")

    def run_chunk(self, idx: int, start: float, end: float, options: dict, timeout: float):
        self.conn.send((idx, start, end, options))
        message = self._wait(timeout, f"transcribing {start:.0f}-{end:.0f} s")
        if message[0] == "error":
            self.stop()
            raise MamError(f"mlx-whisper failed: {message[-1]}")
        return message[2]

    def stop(self) -> None:
        if self.proc is not None:
            if self.proc.is_alive():
                self.proc.kill()
            self.proc.join(timeout=5)
        self.proc = None
        if self.conn is not None:
            self.conn.close()
            self.conn = None


def dominant_language(results: list[dict[str, Any]], default: str = "en") -> str:
    """The language most of the transcribed speech is in."""
    seconds: dict[str, float] = {}
    for r in results:
        if r.get("language"):
            spoken = sum(max(0.0, g["end"] - g["start"]) for g in r["segments"])
            seconds[r["language"]] = seconds.get(r["language"], 0.0) + spoken
    return max(seconds, key=seconds.get) if seconds else default


def find_gaps(results: list[dict[str, Any]]) -> list[tuple[float, float]]:
    """Stretches without any text between two segments, at the start of the recording."""
    spans = sorted(
        (r["offset"] + seg["start"], r["offset"] + seg["end"])
        for r in results
        for seg in r["segments"]
    )
    gaps = []
    for (_, end), (start, _) in zip(spans, spans[1:], strict=False):
        if REPAIR_MIN_GAP_S <= start - end <= REPAIR_MAX_GAP_S and end < REPAIR_UNTIL_S:
            gaps.append((end, start))
    return gaps[:REPAIR_MAX_GAPS]


def run_chunks(
    make_runner: Callable[[], Any],
    chunk_timeout: float,
    fallback_chunk: Callable[[float, float], dict[str, Any]] | None = None,
    allowed_languages: set[str] | None = None,
    default_language: str = "en",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Transcribe every chunk; returns (chunk results with their offsets, events).

    Per chunk: attempt 1 (greedy); on a stall or failure the child is restarted and the chunk
    is retried with the temperature fallback; if that fails too, `fallback_chunk` (whisper.cpp)
    gets it; without one the run fails with a message naming the chunk.

    A chunk whose detected language is not in `allowed_languages` (Whisper hears Turkish or
    Hebrew in kirtan and then decodes everything as that language) is decoded again in
    `default_language`.
    """
    events: list[dict[str, Any]] = []
    runner = make_runner()
    started = time.monotonic()
    runner.start(timeout=max(120.0, chunk_timeout))
    edges = runner.boundaries
    results: list[dict[str, Any]] = []
    try:
        for idx, (start, end) in enumerate(zip(edges, edges[1:], strict=False)):
            began = time.monotonic()
            result = None
            for attempt, options in enumerate(ATTEMPT_OPTIONS, start=1):
                try:
                    if runner.proc is None:
                        runner.start(timeout=max(120.0, chunk_timeout))
                    limit = chunk_timeout * attempt  # the retry may be slower
                    result = runner.run_chunk(idx, start, end, options, limit)
                    break
                except MamError as exc:
                    check_cancelled()  # a stopped job must not retry the chunk
                    events.append(
                        {"chunk": idx, "start": start, "attempt": attempt, "problem": str(exc)}
                    )
                    log.warning("Chunk %d (%.0f s) attempt %d: %s", idx, start, attempt, exc)
            vad = (result or {}).get("vad") or {}
            if vad.get("skipped"):
                events.append({"chunk": idx, "start": start, "skipped": "no speech"})
            elif vad.get("dropped"):
                events.append({"chunk": idx, "start": start, "non_speech_dropped": vad["dropped"]})
            if (
                result is not None
                and not vad.get("skipped")
                and allowed_languages
                and result.get("language") not in allowed_languages
            ):
                events.append(
                    {"chunk": idx, "start": start, "wrong_language": result.get("language")}
                )
                try:
                    result = runner.run_chunk(
                        idx, start, end, {**ATTEMPT_OPTIONS[0], "language": default_language},
                        chunk_timeout * 2,
                    )  # fmt: skip
                except MamError as exc:
                    check_cancelled()
                    events.append({"chunk": idx, "start": start, "problem": str(exc)})
            if result is None and fallback_chunk is not None:
                result = fallback_chunk(start, end)
                events.append({"chunk": idx, "start": start, "used": "whisper.cpp"})
            if result is None:
                raise MamError(
                    f"Transcription failed at {start:.0f}-{end:.0f} s: mlx-whisper stalled or "
                    "failed twice and no whisper.cpp fallback is configured "
                    "(WHISPER_CLI and WHISPER_CPP_MODEL)."
                )
            results.append({"offset": start, **result})
            log.info(
                "Transcribed chunk %d/%d (%.0f-%.0f s) in %.1f s; %.0f s in total",
                idx + 1,
                len(edges) - 1,
                start,
                end,
                time.monotonic() - began,
                time.monotonic() - started,
            )
            report(
                f"Transcribing: part {idx + 1} of {len(edges) - 1}",
                0.05 + 0.85 * (idx + 1) / (len(edges) - 1),
            )
        for n, (gap_start, gap_end) in enumerate(find_gaps(results)):
            try:
                if runner.proc is None:
                    runner.start(timeout=max(120.0, chunk_timeout))
                # a window of its own gets its language detected again (and can be wrong):
                # the language of the rest of the recording is given
                start, end = max(0.0, gap_start - 5.0), gap_end + 2.0
                options = {**REPAIR_OPTIONS, "language": dominant_language(results)}
                fixed = runner.run_chunk(1000 + n, start, end, options, chunk_timeout)
            except MamError as exc:
                check_cancelled()
                events.append({"gap": gap_start, "problem": str(exc)})
                continue
            if fixed["segments"]:
                results.append({"offset": start, **fixed})
                events.append({"gap_repaired": gap_start, "seconds": round(gap_end - gap_start)})
    finally:
        runner.stop()
    return results, events
