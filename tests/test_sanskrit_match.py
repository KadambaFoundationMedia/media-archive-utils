"""Loose matching of chanted Sanskrit against a vedabase.io verse (real Oslo transcript, 2011)."""

from mam.helpers.vedabase import Verse
from mam.tools.class_discoverer.match import (
    SANSKRIT_CONFIRM_SHARE,
    match_sanskrit,
    match_translation,
    skeleton,
)

SB_1_19_31 = Verse(
    transliteration="praśāntam āsīnam akuṇṭha-medhasaṁ\nmuniṁ nṛpo bhāgavato ’bhyupetya\n"
    "praṇamya mūrdhnāvahitaḥ kṛtāñjalir\nnatvā girā sūnṛtayānvapṛcchat",
    translation="The sage Śrī Śukadeva Gosvāmī sat perfectly pacified, possessed of the "
    "intelligence necessary to answer any question without hesitation.",
)
SB_1_9_31 = Verse(
    transliteration="viśuddhayā dhāraṇayā hatāśubhas\ntad-īkṣayaivāśu gatā-yudha-śramaḥ\n"
    "nivṛtta-sarvendriya-vṛtti-vibhramas\ntuṣṭāva janyaṁ visṛjañ janārdanam",
    translation="By pure meditation, looking at Lord Śrī Kṛṣṇa, he at once was freed.",
)
# What Whisper wrote while the verse was chanted (Oslo, 2011-08-20; 00:01:25 onwards)
CHANTED = [
    (85.0, "Munirvipo Bhagavatam Pripyatjam Pranamyamotna Vahita Kritanjalir"),
    (99.2, "Nadvāgyera Sundhita Yanva Pritchat"),
    (108.3, "prasantavasinam akuntamedasam"),
    (116.7, "monim dribho bhagavato vyupetyam"),
    (124.3, "pranámya murdhna vaita kritañjale"),
    (132.5, "Nājvāgīra sunrithayaṁ vāpracca"),
    (140.8, "Prasantamāsinaṁ makuntamedasam"),
    (149.3, "Manimdripo Bhagavata Ubyupetya"),
    (156.6, "Pranāmya-mūdhāvāhitākṛtaṁ jale"),
    (164.7, "Nadvāgīra-sunritāyambha-pritchat"),
]
TALK = [
    (0.0, "Om Namah Bhagavate Vasudevaya"),
    (39.0, "First Canto, Chapter 9, the appearance of Sukadeva Goswami, text 31."),
    (
        500.0,
        "So Maharaja Pariksit was ready to meet his death and approached the spiritual master.",
    ),
    (520.0, "In such cases one is required to approach a spiritual master with folded hands."),
]


def test_skeleton_ignores_vowels_aspiration_and_twin_letters():
    assert skeleton("akuṇṭha-medhasaṁ") == skeleton("akuntamedasam")
    assert skeleton("bhāgavato") == skeleton("Bhagavato")


def test_chanting_matches_its_own_verse_and_not_another():
    own, at = match_sanskrit(SB_1_19_31, TALK + CHANTED)
    other, _ = match_sanskrit(SB_1_9_31, TALK + CHANTED)
    assert own >= SANSKRIT_CONFIRM_SHARE and at is not None and 80 <= at <= 150
    assert other < SANSKRIT_CONFIRM_SHARE and own > other + 0.2


def test_talk_without_chanting_does_not_match():
    share, _ = match_sanskrit(SB_1_19_31, TALK)
    assert share < SANSKRIT_CONFIRM_SHARE


def test_the_chanting_alone_confirms_the_verse():
    got = match_translation(SB_1_19_31, TALK + CHANTED)
    assert got.share < 0.30 and got.sanskrit >= SANSKRIT_CONFIRM_SHARE and got.confirmed
    assert not match_translation(SB_1_9_31, TALK + CHANTED).confirmed
