"""The second pass on the chanted verse (decided 2026-11-22)."""

from mam.helpers.transcriber import Segment, Transcript, verse_pass


def _transcript(n=12):
    segs = [Segment(i * 5.0, i * 5.0 + 4.0, f"line {i}", "en") for i in range(n)]
    return Transcript(segs, "en", "mlx-whisper", None, "m", None, "mlx", {}, "sha", ["en"])


def test_prompt_is_plain_letters_on_one_line():
    text = verse_pass.verse_prompt("praśāntam āsīnam\nakuṇṭha-medhasaṁ")
    assert text == "prasantam asinam akuntha-medhasam"


def test_span_starts_at_the_anchor_with_padding():
    t = _transcript()
    start, end = verse_pass.span_of(t, 10.0)
    assert start == 8.0
    assert end == t.segments[2 + verse_pass.WINDOW_SEGMENTS - 1].end + verse_pass.PADDING_S


def test_span_without_anchor_is_none():
    assert verse_pass.span_of(_transcript(), 12.34) is None


def test_improve_replaces_only_the_chanting(monkeypatch, settings, tmp_path):
    class FakeRunner:
        def __init__(self, *args):
            self.prompt = args[5]
            FakeRunner.seen = self

        def start(self, timeout):
            pass

        def stop(self):
            pass

        def run_chunk(self, index, start, end, options, timeout):
            return {"segments": [{"start": 0.5, "end": 3.0, "text": " prasantam asinam"}]}

    monkeypatch.setattr("mam.helpers.transcriber.backends.pick_backend", lambda s: "mlx")
    monkeypatch.setattr("mam.helpers.transcriber.mlx_runner.MlxRunner", FakeRunner)
    t = _transcript()
    done = verse_pass.improve(tmp_path / "a.mp3", settings, t, "praśāntam āsīnam", 10.0)
    assert done and done["segments"] == 1 and done["segments_before"] > 1
    texts = [s.text.strip() for s in t.segments]
    assert "prasantam asinam" in texts and "line 0" in texts and "line 3" not in texts
    assert FakeRunner.seen.prompt == "prasantam asinam"


def test_improve_does_nothing_without_mlx(monkeypatch, settings, tmp_path):
    monkeypatch.setattr("mam.helpers.transcriber.backends.pick_backend", lambda s: "whispercpp")
    t = _transcript()
    assert verse_pass.improve(tmp_path / "a.mp3", settings, t, "x", 10.0) is None
    assert len(t.segments) == 12
