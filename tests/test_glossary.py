"""The Sanskrit glossary: corrections and the Whisper prompt."""

from mam.helpers.transcriber import Segment, Transcript
from mam.helpers.transcriber.backends import apply_glossary
from mam.helpers.transcriber.glossary import build

ENTRIES = [
    {"source": "Hare Kṛṣṇa", "category": "Mantras", "variants": ["Harry Krishna", "Hari Krishna"]},
    {"source": "Bhagavad-gītā", "category": "Scriptures",
     "variants": ["Bhagwad Geeta", "Bhagwad-Gita", "Bhagavad gita"]},
    {"source": "japa", "category": "Practices", "variants": ["Jap", "Jappa"]},
    {"source": "Śrīla Prabhupāda", "category": "Acharyas / Teachers",
     "variants": ["Shrila Prabhupada", "Srila Prabhupada"]},
]  # fmt: skip


def test_known_variants_are_replaced_whole_words_only():
    g = build(ENTRIES)
    text, n = g.correct("Harry Krishna, then Bhagwad-Gita and bhagwad geeta; Jap is short.")
    assert text == "Hare Kṛṣṇa, then Bhagavad-gītā and Bhagavad-gītā; Jap is short."
    assert n == 3  # 'Jap' is under five letters and is left alone
    assert g.correct("Harry Krishnaa")[1] == 0


def test_a_variant_equal_to_the_source_is_left_alone():
    g = build(ENTRIES)
    # 'Bhagavad gita' folds to the same letters as the source: nothing to correct, no count
    assert g.correct("Bhagavad gita")[1] == 0


def test_prompt_lists_plain_terms():
    prompt = build(ENTRIES).prompt()
    assert "Srila Prabhupada" in prompt and "Bhagavad-gita" in prompt and "japa" not in prompt


def test_transcript_is_fixed_and_counted(settings, tmp_path):
    path = tmp_path / "g.json"
    import json

    path.write_text(json.dumps(ENTRIES), encoding="utf-8")
    cfg = settings.model_copy(update={"sanskrit_glossary_path": path})
    t = Transcript([Segment(0, 1, "Shrila Prabhupada says Harry Krishna", "en")],
                   "en", "x", "1", "m", None, "cpu")  # fmt: skip
    assert apply_glossary(t, cfg) == 2
    assert t.segments[0].text == "Śrīla Prabhupāda says Hare Kṛṣṇa"
    assert t.fallback["glossary_fixed"] == 2
