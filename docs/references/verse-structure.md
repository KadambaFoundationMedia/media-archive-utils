# Verse structure of Vedic Vaisnava scriptures

## Table of contents
- Standard verse structure
- Example - Srimad Bhagavatam 4.4.1
- Verse references in filenames

This describes the verse structure that are used in the books by A.C. Bhaktivedanta Swami Prabhupada. They always follow the same structure.

## Standard verse structure

1. Sanskrit Verse (devanagari)  
2. Sanskrit Verse (roman transliteration)  
3. Synonyms (Word by word english translation)  
4. Translation (English)
5. Purport (by A.C. Bhaktivedanta Swami Prabhupada (explanation))

All the scriptures (see the [https\://vedabase.io/en/](https://vedabase.io/en/) for the full bibliography) follow the same structure. If purports are really long the speaker can choose to split them in multiple parts during the class.

### Example - Srimad Bhagavatam 4.4.1
https://vedabase.io/en/library/sb/4/4/1/

मैत्रेय उवाच  
एतावदुक्त्वा विरराम शङ्कर:
पत्‍न्यङ्गनाशं ह्युभयत्र चिन्तयन् ।
सुहृद्दिद‍ृक्षु: परिशङ्किता भवान्
निष्क्रामती निर्विशती द्विधास सा ॥ १ ॥

maitreya uvāca
*etāvad uktvā virarāma śaṅkaraḥ*
*patny-aṅga-nāśaṁ hy ubhayatra cintayan*
*suhṛd-didṛkṣuḥ pariśaṅkitā bhavān*
*niṣkrāmatī nirviśatī dvidhāsa sā*

**Synonyms**  
*maitreyaḥ* *uvāca* — Maitreya said; *etāvat* — so much; *uktvā* — after speaking; *virarāma* — was silent; *śaṅkaraḥ* — Lord Śiva; *patnī*\-*aṅga*\-*nāśam* — the destruction of the body of his wife; *hi* — since; *ubhayatra* — in both cases; *cintayan* — understanding; *suhṛt*\-*didṛkṣuḥ* — being anxious to see her relatives; *pariśaṅkitā* — being afraid; *bhavāt* — of Śiva; *niṣkrāmatī* — moving out; *nirviśatī* — moving in; *dvidhā* — divided; *āsa* — was; *sā* — she (Satī).

Translation

**The sage Maitreya said: Lord Śiva was silent after speaking to Satī, seeing her between decisions. Satī was very much anxious to see her relatives at her father’s house, but at the same time she was afraid of Lord Śiva’s warning. Her mind unsettled, she moved in and out of the room as a swing moves this way and that.**

Purport  
Satī’s mind was divided about whether to go to her father’s house or obey the orders of Lord Śiva. The struggle between the two decisions was so strong that she was pushed from one side of the room to another, and she began to move just like the pendulum of a clock.

## Verse references in filenames
The WHAT in a filename (see filenaming-convention.md) starts with the scripture abbreviation and the verse numbers, separated by hyphens. The same reference gives the vedabase.io URL used to confirm the verse.

| Scripture | Filename WHAT | vedabase.io URL |
|---|---|---|
| Bhagavad-gita, chapter 2, verse 51 | `BG-2-51` | https://vedabase.io/en/library/bg/2/51/ |
| Srimad Bhagavatam, canto 4, chapter 4, verse 1 | `SB-4-4-1` | https://vedabase.io/en/library/sb/4/4/1/ |
| Caitanya-caritamrta, Madhya-lila, chapter 20, verse 154 | `CC-Madhya-20-154` | https://vedabase.io/en/library/cc/madhya/20/154/ |
| Srimad Bhagavatam, canto 9, chapter 20, verses 20 to 27 | `SB-9-20-20-27` | https://vedabase.io/en/library/sb/9/20/20/ to .../sb/9/20/27/ |

### Verse ranges
A class can discuss a sequence of verses. The range is written as the first verse followed by the last verse:
- `SB-9-20-20-27`: canto 9, chapter 20, verses 20 to 27
- `BG-2-51-53`: chapter 2, verses 51 to 53
- `CC-Madhya-20-154-156`: Madhya-lila, chapter 20, verses 154 to 156

How many numbers a single verse has depends on the scripture, so any extra number is the end of a range:

| Scripture | Single verse | Range |
|---|---|---|
| BG | chapter-verse (2 numbers) | 3 numbers |
| SB | canto-chapter-verse (3 numbers) | 4 numbers |
| CC | lila-chapter-verse (lila + 2 numbers) | lila + 3 numbers |

- The last verse is always higher than the first; otherwise the number is not a range and the file is flagged for review.
- A title after the verse contains words, not numbers (`SB-9-20-20-27-Dont-waste-time`), so it never looks like a range.
- Each verse in the range can be checked on vedabase.io.

- The CC lila is written `Adi`, `Madhya` or `Antya`.
- Source names use many variants of the same abbreviation (`S.B.`, `SB`, `Srimad-bhagavatam`, `B.G.`, `CC`, `Chaitanya Charitamrita`). The accepted variants per category are in the Baserow `category_terms_folder` table.
- Some verses have no purport (e.g. CC Madhya 20.154); the topics of such a class come from the transcript.
