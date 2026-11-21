"""Chunked, watched transcription: stalls are retried, loops are collapsed."""

import numpy as np
import pytest

from mam.errors import MamError
from mam.helpers.transcriber.chunking import (
    SAMPLE_RATE,
    collapse_loops,
    looks_looped,
    pick_boundaries,
)
from mam.helpers.transcriber.mlx_runner import ChunkStalled, run_chunks


def test_boundaries_move_to_the_quietest_spot():
    audio = np.ones(SAMPLE_RATE * 700, dtype="float32") * 0.5
    audio[SAMPLE_RATE * 310 : SAMPLE_RATE * 311] = 0.0  # a pause near the 300 s edge
    edges = pick_boundaries(audio, 300)
    assert edges[0] == 0.0 and edges[-1] == 700.0
    assert 310 <= edges[1] <= 311
    assert all(b > a for a, b in zip(edges, edges[1:], strict=False))


def test_no_tiny_last_chunk():
    edges = pick_boundaries(np.ones(SAMPLE_RATE * 320, dtype="float32"), 300)
    assert edges == [0.0, 320.0]


def test_loops_are_collapsed_but_speech_is_kept():
    looped = "I give you the smile, " + "the smile, " * 20 + "now"
    assert looks_looped(looped * 3)
    assert collapse_loops(looped) == "I give you the smile, now"
    speech = "Hare Krishna Hare Krishna we are reading from Bhagavad-gita chapter two"
    assert collapse_loops(speech) == speech  # two in a row can be speech


class FakeRunner:
    """Stalls on chunk 1 the first time, like the Sweden recording did."""

    def __init__(self, stall_always=False):
        self.proc, self.boundaries, self.stall_always, self.calls = (
            None,
            [0.0, 300.0, 600.0],
            stall_always,
            [],
        )

    def start(self, timeout):
        self.proc = object()

    def run_chunk(self, idx, start, end, options, timeout):
        self.calls.append((idx, options.get("compression_ratio_threshold")))
        if idx == 1 and (self.stall_always or len(self.calls) == 2):
            self.proc = None
            raise ChunkStalled("no progress")
        return {"language": "en", "segments": [{"start": 1.0, "end": 2.0, "text": f"c{idx}"}]}

    def stop(self):
        self.proc = None


def test_a_stalled_chunk_is_retried_with_the_temperature_fallback():
    runner = FakeRunner()
    results, events = run_chunks(lambda: runner, 10.0)
    assert [r["offset"] for r in results] == [0.0, 300.0]
    assert runner.calls == [(0, None), (1, None), (1, 2.4)]  # the retry uses the thresholds
    assert events[0]["chunk"] == 1 and events[0]["attempt"] == 1


def test_whispercpp_takes_over_when_both_attempts_fail():
    runner = FakeRunner(stall_always=True)
    cpp = lambda start, end: {"language": "en", "segments": [{"start": 0, "end": 1, "text": "cpp"}]}  # noqa: E731
    results, events = run_chunks(lambda: runner, 10.0, cpp)
    assert results[1]["segments"][0]["text"] == "cpp"
    assert any(e.get("used") == "whisper.cpp" for e in events)


def test_without_a_fallback_the_failure_names_the_chunk():
    with pytest.raises(MamError, match="300-600"):
        run_chunks(lambda: FakeRunner(stall_always=True), 10.0)


def test_an_unreliable_transcript_is_not_a_sure_no_verse():
    from mam.helpers.transcriber import Segment, transcript_quality
    from mam.tools.class_discoverer.tool import decide

    bad = [Segment(i, i + 1, "Thank you.") for i in range(8)] + [Segment(9, 10, "ok")]
    quality = transcript_quality(bad, {"loops_collapsed": 2})
    assert quality["unreliable"]
    got = decide({"found": None, "file": None, "quality": quality})
    assert got["action"] == "none" and not got["sure"] and "unreliable" in got["reasons"][0]
    good = transcript_quality([Segment(0, 5, "Reading from the Gita")] * 10, None)
    assert decide({"found": None, "file": None, "quality": good})["sure"]


def test_a_chunk_heard_as_turkish_is_decoded_again_in_english():
    class Runner(FakeRunner):
        def run_chunk(self, idx, start, end, options, timeout):
            self.calls.append((idx, options.get("language")))
            lang = options.get("language") or "tr"
            return {"language": lang, "segments": [{"start": 1.0, "end": 2.0, "text": "x"}]}

    runner = Runner()
    results, events = run_chunks(lambda: runner, 10.0, None, {"en", "nl"}, "en")
    assert [r["language"] for r in results] == ["en", "en"]
    assert runner.calls == [(0, None), (0, "en"), (1, None), (1, "en")]
    assert events[0]["wrong_language"] == "tr"


def test_zero_length_segments_are_dropped():
    from mam.helpers.transcriber.backends import _mlx_segments

    segs = _mlx_segments(
        {"segments": [{"start": 5.0, "end": 5.0, "text": "Bir dey"},
                      {"start": 6.0, "end": 8.0, "text": "real"}]},
        False,
    )  # fmt: skip
    assert [s.text for s in segs] == ["real"]


def test_invented_credits_are_recognised_and_counted():
    from mam.helpers.transcriber import Segment, transcript_quality
    from mam.helpers.transcriber.chunking import is_invented_credit

    assert is_invented_credit("Altyazı M.K.") and is_invented_credit("Shabbat Shalom")
    assert not is_invented_credit("Reading from Srimad Bhagavatam")
    q = transcript_quality([Segment(0, 1, "text")], {"invented_dropped": 3})
    assert q["bad_share"] == 0.75 and q["unreliable"]


def test_a_gap_at_the_start_is_decoded_again_without_the_no_speech_check():
    from mam.helpers.transcriber.mlx_runner import REPAIR_OPTIONS, find_gaps

    results = [
        {"offset": 0.0, "segments": [{"start": 10.0, "end": 12.0}, {"start": 50.0, "end": 52.0}]},
        {"offset": 300.0, "segments": [{"start": 5.0, "end": 6.0}]},
    ]
    assert find_gaps(results) == [(12.0, 50.0)]  # 38 s of nothing; the 250 s after is too late

    class Runner(FakeRunner):
        def run_chunk(self, idx, start, end, options, timeout):
            self.calls.append((idx, options.get("no_speech_threshold", "default")))
            if idx >= 1000:
                assert options == REPAIR_OPTIONS and (start, end) == (10.0, 52.0)
                return {"language": "en", "segments": [{"start": 3.0, "end": 8.0, "text": "tr"}]}
            segs = [
                {"start": 10.0, "end": 12.0, "text": "a"},
                {"start": 50.0, "end": 52.0, "text": "b"},
            ]
            return {"language": "en", "segments": segs if idx == 0 else []}

    runner = Runner()
    results, events = run_chunks(lambda: runner, 10.0)
    assert any("gap_repaired" in e for e in events)
    assert results[-1]["offset"] == 10.0 and results[-1]["segments"][0]["text"] == "tr"
