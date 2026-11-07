# Changelog

All notable changes are recorded here. The project has one version number (`mam/__init__.py`,
shown by `mam --version` and in the portal footer). It follows [Semantic Versioning](https://semver.org/):
a new tool or feature raises the minor number, a fix raises the patch number, and a change that
breaks stored data or commands raises the major number. Each entry names the component it touches.

## [Unreleased]

### Kirtan splitter
- New tool: finds where the kirtan ends in a recording that is kirtan followed by a class, from the audio alone (a local voice-activity model for the speech, loudness and treble for where the music stops, and silences; no transcription), and splits it into a kirtan file (named by mantra type) and the class. Runs after the Renamer; in the Action menu and as `mam kirtan-split <file>` (dry run by default, prints a timeline).
- Music throughout or no kirtan: not split. Unsure cuts open a review with a page to edit the time; sure ones (the music clearly stops, then speech; a kirtan of 1.5 to 35 minutes) are cut automatically, with 5% spot checks. The score is shown but no longer decides.
- A long kirtan followed by less than 3 minutes of talk (closing words) is a kirtan recording and is not split.
- Faster: the audio is decoded once instead of three times (about 30% less time per file, same results), and several files are analysed at the same time on the CPU pool (up to 8 workers, one per core minus 2).
- Class recordings finish sooner: the analysis stops once the class has lasted 5.5 minutes after the kirtan, because nothing later can change the cut. The decision looks at the first 5 minutes of the class only. Recordings that are not clear-cut are still analysed in full.
- Waveform editor on the review page: the kirtan and the class as two colored regions; drag the edge between them, nudge it by 0.1 or 1 s, cut at the playhead or type the time, and listen 3 s before, around or after the cut. A long recording opens after a one-time preparation (peaks and, for WMA and video, a small mp3 preview, kept in the work folder).
- Fix: a class that opens with a sung mantra is no longer cut 40 to 55 minutes in. The kirtan before a class must be a stretch of at least 90 s that is mostly music (70%), not music seconds scattered through a long class. A short sung opening (up to 90 s) belongs to the class. A recording of bhajans that begins with an announcement, or has talks in between with music after them, is a kirtan recording. The review reasons now say what was found at the start.
- Video recordings: the audio is extracted as mp3 first.
- Common phrases are ignored for now (they need a transcript).

### Media manager
- The file page and the Renamer review page are one page, `/details/<id>`: a file with a pending review shows the reasons and Approve / Reject / Exclude, other files show Save (so a finished file can still be corrected and updated in Baserow). Old `/files/<id>` and `/review/<id>` links redirect. The Kirtan splitter review is on that page too; saving the name there no longer closes it.
- New "Do not create row" choice under "Baserow media row": saves or approves without writing to Baserow. Preselected while "Baserow writes" is off.

### Project
- Versioning and this changelog started.

## [1.0.0] - 2026-11-01

First approved release: the helpers, the media manager and the Renamer.

### Media manager
- Web portal (`uv run mam serve`) with file list, filters, file page, reviews, jobs and settings.
- Job queue with live updates, Baserow switch (live or dry run), reset with automatic backup.
- Place and country lists from Baserow, with `place_country` aliases.

### Renamer
- Finds date, WHAT and WHERE from filename, folders, tags, sibling files, the Baserow media table and
  the travel schedule; copies to processed-files and opens reviews when unsure.
- Hand-written aliases beat real places; `Summercamp` gives the Summercamp category.

### Helpers
- Baserow, file organizer, local database and audio optimizer.
