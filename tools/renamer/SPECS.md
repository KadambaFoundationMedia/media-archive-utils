# Renamer Tool

## Table of contents
- Goals
- Requirements
- Non standard filenames
- Examples of possible filenames
- Filename evidence and confidence
- mp3 tag information
- Travel schedule
- Parent (sub)folder evidence
- Filename convention (docs/references/filenaming-convention.md)
- Multiple languages

## Goals
The main goal is to rename files in the media archive according to our filenaming convention. The filename provides the first, and best, evidence of the date, type and location for that recording. When the filename does not provide all the required evidence, the parent folder name should be checked for any clues on a possible date, location and type. Next to check if the mp3 tag information inside mp3 files for any evidence. And last but not least, if evidence is still lacking the travel schedule table and media table in our baserow database could provide more insight. Files that have a high confidence should be renamed automatically. Files with low confidence should be flagged and added for review to the review portal. 

## Requirements
- Use local utilities for mp3 tag scanning
- Rename the file at their the original location
- Add a dry-run option

## Non standard filenames
The challenge is that many files do not follow any convention and in worst cases the date is missing or is not specific. We also have multiple date formats (US/EU/EN/NL) mixed together. In other cases the date is abbreviated. And in another case they can have non-descriptive filenames, like "R09_0003.mp3" but they are grouped together in a folder that gives a clue on the date/location.

### Examples of possible filenames
- KKS_Janmastami evening lecture_03-09-2018_Slovenia.m4a
- SB 10.3.11 Janmastami class - HH KKS - Slovenia 15.8.17.MP3
- KKS_SB-10-2-18_Janmastami_2006_germany.wma
- KKS_SB-1.8.30-and-Kirtan_Janmastami-Aug-2009_Korsnas-Gard_Swe_CORRUPTED.mov
- 2020-08-12_KKS-Janmastami-SB-10-3_Radhadesh_be.m4a
- 2010-09-02_KKS-Janmastami_zagreb_croatia.mpg
- HH KKS BG 7.17 2008-04-12.mp3
- 2008-01-04-2.mp3
- R09_0004.MP3
- HH KKS SB 3.1.28. 081003.mp3 (in a folder called "HH KKS okt 2008"

## Filename evidence and confidence
The filename provides the best evidence for the type of recording and provides the highest confidence. The second best source is the media table, and the last source is the travel schedule. All three should provide enough information to rename the file according to our filenaming convention.

## mp3 tag information
The MP3 tag information embedded in media files could also provide metadata. The confidence for that data is medium to high.

## Travel schedule
The travel_schedule does not provide 100% proof that the speaker/recording was at that location. If there is a date and/or a location in the filename this should always take precedence. In those cases the travel_schedule can be used to provide further proof.

## Parent (sub)folder evidence
It is possible that the filename won't provide all the required information but the (sub)folder might provide evidence on its location or date. For example: 

The folder/filename could be "KKS DUBEN 2008 MP3/Lekce/06 KKS SB 3.1.25.mp3". In this example the filename does not provide a date, but the parent folder "DUBEN 2008" translates to April 2008. And the subfolder "Lekce" translates to "class". This provides more concrete evidence for the final rename.

## Filename convention (docs/filenaming-convention.md)
All files must follow this strict naming convention. Files that cannot be renamed, due to missing information, should be flagged for further processing. If consecutive tooling provides more metadata the filename should be corrected.

## Confirmed values and enrichment by other tools
What a person saves on the file page or approves in a review is *confirmed*. Later runs of the Renamer keep confirmed values and only fill fields that are still empty (or complete a date that has placeholders).

Other tools call the Renamer with what they discovered, as job parameters:

```python
enqueue(
    session,
    "renamer",
    [file_id],
    dry_run=False,
    params={"evidence": {"what": "BG-2-51", "source": "class-discoverer"}},
)
```

Evidence fields: `when`, `what`, `place`, `place_name`, `cc`, `category`, `verse_tag`, `title`. Evidence takes precedence over the Renamer's own guesses. If it contradicts a confirmed value, the file is flagged for review instead of overwritten.

## Multiple languages
The speaker, Kadamba Kanana Swami, speaks multiple languages; English, Dutch, German, Spanish, Hindi. French. And some recordings have live translations in languages; Czech, Slovak, French, German, Spanish and others). Some filenames also are in different languages, like Czech and others. 

