# Media file naming convention

## Table of contents
- YYYY-MM-DD
- The 3 WWW
- Partial information
- File naming best practices
- Clashes and duplicates
- Correcting messy source names

## YYYY-MM-DD
Each filename **must** start with: **YYYY-MM-DD**  
This is an [ISO date standard](https://en.wikipedia.org/wiki/ISO_8601) 

### Incomplete or missing dates?
Substitute the date with placeholders, YYYY, MM and DD.  
Example: `2015-MM-DD_KKS_Jaya-radha-madhava.mp3` (year known, month and day unknown).

### Reading dates in source names
- **European order first:** a date like `05-09-11` or `11.07.2008` is read as day-month-year. Month-day-year or year-month-day is only chosen when other evidence points to it (the folder name, other files in the folder, the ID3 tags, the travel schedule).
- **ID3 dates are trusted:** a date in the file's ID3 title tag or ID3 date tag is taken as correct unless other evidence contradicts it. When it contradicts the folder name, the file is flagged for review.
- **Travel days:** when the travel schedule has KKS leaving one place and arriving in another on the recording date, the place is left open and both are shown for the review.

## The 3 WWW
Each filename must provide the **WHO, WHAT and WHERE.**   
Do not use placeholders for missing information.

WHO: The acronym of the speaker. This is always `KKS`.  
WHAT: The scripture and verse, plus a short title (see "WHAT" below)  
WHERE: Place and country (ccTLD)

Example 1: 2013-11-14_KKS_SB-9-20-20-27_Vrindavan-in.mp4  
WHO: KKS  
WHAT: SB-9-20-20-27 (Srimad Bhagavatam, canto 9, chapter 20, verses 20 to 27)  
WHERE: Vrindavan-in

Example 2: 2023-01-16_KKS_CC-Raghunatha-das-goswami_Amsterdam-nl.mp3  
WHO: KKS  
WHAT: CC on Raghunatha das goswami  
WHERE: Amsterdam-nl

### WHAT
- Start with the scripture abbreviation and verse, e.g. `SB-9-20-20`, `BG-2-51`, `CC-Madhya-20-154` (see verse-structure.md).
- A sequence of verses is written as the first and last verse: `SB-9-20-20-27` is canto 9, chapter 20, verses 20 to 27 (see "Verse ranges" in verse-structure.md).
- Add the short title from the content-discoverer after the verse, e.g. `SB-9-20-20-Dont-waste-time`.
- Recordings without a verse (festivals, seminars, kirtans) use a title or the type only, e.g. `Janmastami-evening-lecture` or `Jaya-radha-madhava`.
- Kirtan files use the mantra type: `Kirtan` (maha-mantra), `Jaya-radha-madhava`, `Jaya-Jaya-Sri-Caitanya`, `Nrsimha-pranama`.

### WHERE
WHERE has one of three forms:
- `Place-cc`: the place in Title-case and the ccTLD in lowercase, e.g. `Oslo-no`, `Radhadesh-be`
- `cc`: the country only, when the place is unknown, e.g. `fi`
- omitted, when nothing is known

The ccTLD must be a valid country code (`no` for Norway, `se` for Sweden, `in` for India).

## Partial information
A file is always renamed with whatever is known. A WHO, WHAT or WHERE that is not known is left out, never replaced by a placeholder; only the date uses placeholders.

| Known | Filename |
|---|---|
| Date only | `2008-01-04_KKS.mp3` |
| Date and country | `2011-08-20_KKS_fi.mp3` |
| Year, kirtan type | `2015-MM-DD_KKS_Jaya-radha-madhava.mp3` |
| Everything | `2013-11-14_KKS_SB-9-20-20-27_Vrindavan-in.mp4` |

## File naming best practices
* The separator between WHEN, WHO, WHAT and WHERE is always an underscore.  
* The inside separator  of WHEN, WHO, WHAT and WHERE is always a hyphen.  
* File names should be short but descriptive (between 25-128 characters)  
* Titles should be short and conscise  
* Use only 1 dot in the filename  
* File extension must be lowercase  
* The extension follows the file's real format. Split and trimmed files keep the source codec, so a `.wma` source gives `.wma` outputs.
* Avoid special characters or spaces in a file name like:  **\!@\#\$%^&\*()+=\[\]{}\\|:;”‘\<\>?/**

## Clashes and duplicates
- Recordings from the same day are told apart by their WHAT (e.g. a morning SB class and an evening BG class).
- If a different file already has the exact same name, add `-2`, `-3`, ... before the extension, e.g. `2018-01-09_KKS_SB-9-20-20_Vrindavan-in-2.mp3`. The file is flagged for review.
- If a file with the same name and the same size already exists, save it with `_duplicate`, e.g. `2018-01-09_KKS_SB-9-20-20_Vrindavan-in_duplicate.mp3`.

## Correcting messy source names
Source names in the archive are often inconsistent. The tools check each part against known values (ISO country codes, places in the Baserow `travel_schedule`, Baserow categories and verse patterns):
- **One valid correction:** it is applied and recorded in the Baserow `notes`, e.g. `oslo-noi` becomes `Oslo-no`.
- **No valid correction, or several:** the file is flagged for review.
- **Parts with no meaning:** sequence numbers and recorder names from the source (`-2`, `ZOOM0004`, `R09_0003`) are not kept in the new name. The original name is always kept in the local database and in Baserow.
- **Glued words are split:** `RadhastamiBhajanas` becomes `Radhastami-Bhajanas`, `RadhaMadhava` becomes `Radha-Madhava`.
- **Honorifics and recorder words are dropped:** `HH`, `Kadamba Kanana Swami` (that is the WHO), `STEREO`, `clean`. Names of other people keep their honorific (`Suhotra-Maharaja`).
- **Generic words next to a verse are dropped:** `SB 3.1.20 class` gives `SB-3-1-20`. Without a verse they stay (`Janmastami-evening-lecture`).
- **Places use their short name:** the Baserow place `ISKCON-Ljubljana` is written `Ljubljana-si`; when the Renamer reads source names, known spellings are mapped (`Praha` to `Prague`, `Koln` to `Cologne`, `Rdesh` to `Radhadesh`).
- **A place typed by a person is taken literally:** on the file page `Amsterdam` stays `Amsterdam` (a new place) and is never swapped for `ISKCON-Amsterdam`; the page suggests the known place it may mean.
- **Another speaker in the name** (`SNS`, `BVS` instead of `KKS`) is flagged for review, because WHO is always `KKS`.
- **Repeated corrections:** a correction reviewers make the same way at least 3 times becomes a rule, so it is applied automatically from then on.
