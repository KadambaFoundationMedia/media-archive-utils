# Changelog

All notable changes are recorded here. The project has one version number (`mam/__init__.py`,
shown by `mam --version` and in the portal footer). It follows [Semantic Versioning](https://semver.org/):
a new tool or feature raises the minor number, a fix raises the patch number, and a change that
breaks stored data or commands raises the major number. Each entry names the component it touches.

## [Unreleased]

### Fixed
- The Settings page no longer warns about a place option that is only another spelling (an alias) of a general place without a country, such as `Sumercamp` for `Summercamp`.

### Safety
- An empty folder setting in `.env` (for example `MEDIA_IDENTICAL_FILES_PATH=`) now means the default folder; it used to become the repository folder, which made the Settings page count the whole repository as processed copies. The reset also refuses any folder that is or contains the repository or the folder of the local database.

### Settings
- The Settings page spreads its blocks over both columns: the tool blocks on the left in pipeline order (Renamer, Kirtan splitter, Class discoverer, Content discoverer, as in CONTRACTS.md; the Class trimmer joins when it is built), and Review, General, Advanced, Current settings and Reset on the right.
- `.env.example` is shortened to the private values and paths; the other options are set on the Settings page. The test now checks that every key in it is a real setting.

### Content discoverer
- New tool: reads the class transcript and writes 1 to 5 themes, a short title and a YouTube description (with the footer and the current year). Run it from the Action menu or with `mam content-discover <file>` (dry run by default; a dry run asks the model once and caches the answer, so the real run is not billed again).
- The themes, description and `youtube_desc` go to the Baserow media row (empty fields only; a different existing value goes to review). A class with a verse and no title gets the title in its filename through the Renamer. A recording without a verse keeps its filename.
- The first 20 results (Settings: "Results checked first") are reviewed on the file page, where the themes, title and description can be edited; after that it works alone, with the random check.
- New LLM helper with a pluggable provider (the Claude API for now; set `ANTHROPIC_API_KEY` in `.env`). The model and its thinking effort are options on the Settings page. Answers are cached by input and prompt version.

- Review of the first result: themes can no longer be broad words. The Settings block "Content discoverer" has a list of **Broad themes** (never used: the model is told, and a match in its answer is dropped) and a **YouTube footer** field (the text file stays the fallback). The footer is shown under the description on the review page.
- Fewer, truer themes: the prompt asks for as many as the content needs (usually 2 or 3), each a topic the description also covers, in correct English word order ("Humble Inquiry", never "Preaching Art"). The title and description follow where most of the recording's time goes (a discussion with the listeners counts).
- The description starts with the recording's type, verse, date and place in natural language, from the file's own data; a description without the place or year is sent to review.
- Names are spelled as in the Sanskrit glossary ("Caitanya", not "Chaitanya"): the glossary's spellings are given to the model and its answer is corrected afterwards.
- The run shows what it cost (tokens and dollars; the prices are Settings options). Prompt version `content-2`: earlier cached answers are not reused.
- The file page shows the vedabase.io link of the verse once the Class discoverer has run.

### Class discoverer
- Removed the experimental "Improve the chanted verse" option (a second transcription pass on the chanted verse); the test on real recordings was mixed.
- Other-language words for chapter and text can be added in the Advanced block of the Settings page, so a verse announced in Dutch, Spanish and so on can be read when the numbers are spoken as digits or English words. Accented letters in the transcript are no longer split.

### Media manager
- Scanning and importing reads the fingerprints of new files several at a time, so a folder on the NAS is registered about 5 times faster (measured with 0.2 s per file read).
- Reset keeps the values set on the Settings page; only the Baserow writes switch is turned off again.

### Renamer
- The earliest year read as a date (1990) is an option in the Advanced block of the Settings page.
- Acronyms of other speakers and the words dropped from names are options in a new Advanced block on the Settings page.
- The class discoverer reads Whisper language codes from the `whisper_code` column of Baserow `language_terms` (built-in list only as a fallback while the column is empty).
- The recorder file name prefixes (ZOOM, DSC, TASCAM, ...) and the recorder album tags that are ignored are now options on the Settings page (Renamer block), not code.
- Summercamp now comes from a row in `category_terms_folder` (add the row and its spellings in Baserow), not from a pattern in the code. A recorder file in a Summercamp folder is named `..._Summercamp_...` and is no longer flagged.

### Renamer
- Faster on big folders: the names of the other files in a folder are parsed once per folder instead of once per file (a 400-file folder: 46 ms to 4 ms per file). Small clean-ups of two unused helper functions.

### Kirtan splitter
- A kirtan split off a class is now always named `Jaya-radha-madhava` (it was `Jaya-Jaya-Sri-Caitanya` before a CC class). A recording that is singing throughout stays `Kirtan`.

### Class discoverer
- A recording in which no verse is heard now goes to review when the file's only WHAT or category is a general one (`Class`, `Various`, `Unknown`), instead of being marked Done.

### Media manager
- Settings page: the non-secret options that were only in `.env` are now set there, with autosave: Class discoverer (glossary fix and prompt, word timestamps, chunk length and timeout, Whisper languages, delay between vedabase.io requests), Kirtan splitter (early stop, analysis in separate processes), Review (random check rate) and General (operator name, worker counts; the workers apply after a restart). The `.env` value is the starting value. `START_THRESHOLD` is removed (nothing read it).

### Renamer
- Which categories are kirtans now comes from the new `type` column of `category_terms_folder` (`class` or `kirtan`), not from a list in the code. This applies to the Bhajans-over-event rule, the Kirtan folder for kirtan recordings, the mantra choices on the splitter review and the dropped "with <kirtan>" phrase.

### Kirtan splitter
- Clear statuses: a file the splitter handled shows `Splitter: Class` (no kirtan before the class), `Splitter: Kirtan` (singing throughout) or `Splitter: Done`, all green, instead of `Skipped` or `Kirtan splitter Done`. When a reviewer chooses *Do not split* on a recording that the splitter wanted to split, the status is `Splitter: Not split`. Existing files are converted by a database migration.
- A kirtan recording (singing throughout) is now filed in the Kirtan folder. A name such as `Radhastami-Bhajanas` gave the category Radhastami, but only classes go in the Radhastami and Nrsimha-caturdasi folders. The splitter marks the recording and queues the Renamer, which moves the copy and sets the category Kirtan (the file keeps its splitter status). A WHAT that is itself a kirtan category (`Nrsimha-pranama`, `Jaya-radha-madhava`) keeps its own folder.

### Renamer
- Bhajans are always Kirtan: `Radhastami-Bhajanas` is filed under Kirtan, not Radhastami (unless it is a combination: a verse first, or `with`/`and`/`plus` before the word).

### Media manager
- The statuses `Queued <Tool>` and `Processing <Tool>` are merged into `Queued` and `Processing`; the tool is shown in the file list's job column.

## [1.1.0] - 2026-11-22

### Kirtan splitter
- After a split, the class loses a "with Radha Madhava" phrase in its name (`SB-1-19-31-with-radha-madhava` becomes `SB-1-19-31`), because it is no longer a combination recording. The Renamer is queued with the cleaned WHAT.

### Class discoverer
- New Settings option "Improve the chanted verse" (off by default): after the verse is confirmed, the chanting is transcribed a second time with the verse's transliteration from vedabase.io as Whisper's prompt.

### Media manager
- The Run and Preview buttons now have short tooltips ("Process file(s)" and "Preview mode. No files are changed") instead of the long explanation.
- Live progress: a running job shows what it is doing. The file list has a spinner (a clock while queued) next to the status with the step in a few words and the percent ("Transcribing 3/14 21%", "Loudness 2%"), updating live; the Jobs page shows the step and a progress bar; the file page names the step. Reported by the Class discoverer and the Kirtan splitter. The progress bar uses the Material 3 primary color in light and dark.
- A banner asks for a restart when the program files changed after the portal started (a `git pull` while it ran made jobs fail with an import error).
- Running the Renamer again on a file it has already confirmed no longer stops a pipeline run: the next tool is queued.

### Renamer
- A country code that contradicts a place Baserow knows with one country (`Oslo_fi`) is corrected to the place's country (`Oslo-no`) without a review, and noted in the Baserow `notes`.

### Class-discoverer
- The chanted Sanskrit now confirms a verse too: it is compared with the verse's transliteration on vedabase.io by its consonants, so Whisper's by-ear spelling still matches (Oslo: 92% for the right verse, 20% for the wrong one). The review shows "Sanskrit heard".
- Settings page, block "Class discoverer": "Only transcribe speech" (off by default). Parts without speech are not given to Whisper and text written over non-speech is dropped, which removes most invented text and saves time.
- Sanskrit glossary: known Whisper misspellings of Sanskrit names and terms (`Harry Krishna`, `Bhagwad Geeta`) are replaced by the glossary's spelling in the transcript. Optionally the terms are given to Whisper as its prompt (`TRANSCRIBE_GLOSSARY_PROMPT`, off).
- Transcripts are written as `.srt` (SubRip) instead of Markdown. A transcript already written as `.md` still follows its audio.
- When the announced verse differs from the filename's, and only the filename's verse is heard on vedabase.io, the filename is kept without a review. The review says how much of each translation was heard and marks an unconfirmed verse on its button.
- A spoken correction up to 20 seconds after the announcement is read ("chapter 9 ... no, chapter 19").
- The log has a line for each vedabase.io page fetched and one for each verse check.
- Transcription: a stretch of 20 to 90 s without text in the first 15 minutes (Whisper skipped it as "no speech", so the read translation of the verse was missing) is decoded again with the no-speech check off.
- The verse announcement is read when Whisper writes "3rd Kanto" or "Kanchu", and when the speaker corrects a number ("chapter 9, no, chapter 19"): the later number wins and the earlier reading is tried on vedabase.io if the later one is not confirmed.
- Transcription is now chunked and watched: a stalled chunk is stopped and retried (temperature fallback, then whisper.cpp), repetition loops are collapsed, each chunk is logged, and a transcript full of repeated or invented lines sends "no verse heard" to review instead of Done. New settings `TRANSCRIBE_CHUNK_S` and `TRANSCRIBE_CHUNK_TIMEOUT_S`.
- A transcript now follows its audio: when the Renamer renames the copy or files it in another category folder (also after a Save in the portal), the transcript moves and is renamed with it.
- Transcripts are saved in the matching category subfolder of `transcriptions/`, the same folder the audio copy has in processed-files.
- A verse only counts as the class verse when it is announced in the first 10 minutes and before the Translation or Purport is read, so a verse quoted in the purport can no longer win.

### Media manager
- Settings page: the "File list" block is now called **Filters** and has a new option **Default Show** (50, 100, 200 or 500). The file list's Show selector starts on it and Reset all returns to it. It saves by itself.
- Settings page, Reset block: new checkbox **Also delete all earlier database backups** (also `uv run mam reset --old-backups`). It removes the older `mam-export-*` folders in `~/.mam/backups`; the backup made by that reset is always kept. Off by default.
- Settings, Reset: with "Delete the processed copies" the reset also removes the transcript files in `transcriptions/` (the page shows how many). Cached transcripts in the database stay unless "Also clear caches" is ticked, so a re-run does not transcribe again.
- Jobs page: a Scan folder result now has collapsible lists. **Identical copies** are grouped by folder and show which registered file each one duplicates; **other files ignored** are grouped by file type with counts. The lists keep the first 2000 of each (the counts stay complete).
- Settings page: options now save by themselves when you change them (a check mark shows, or the problem if a value is refused), so there are no Save buttons any more.

### Renamer
- Language words (`GER`, `deutsch`, `nederlands`...) and country words (`Holland`, `UAE`, `Cesko`...) now come from the new Baserow tables `language_terms` and `country_terms`, and the places that are no real location or have no country from the `not_place` and `no_country` checkboxes of `place_country`. The built-in lists are gone. Add `BASEROW_LANGUAGE_TERMS_TABLE_ID` and `BASEROW_COUNTRY_TERMS_TABLE_ID` to `.env` (see `.env.example`).
- Place spellings (`Praha`, `Koln`, `Rdesh`, `NYC`...) now come from the `Aliases` column of `place_country`, and the class words dropped next to a verse (`SB 3.1.20 class`) from the terms of the `Class` category. The built-in lists are gone, and so is the one-off place data of `mam place-country sync`.
- New **Class** category (from the `category_terms_folder` table): a file in a `Lekce`, `Lecture`, `Class` or `Lezing` folder with no other clue is named `..._KKS_Class.mp3` and filed in the `class` folder, until the class-discoverer finds the real type.
- Category words now come only from the `category_terms_folder` table (the built-in word list in `aliases.yaml` is gone).
- A file whose name gives no WHAT (`R09_0008.MP3`, `ZOOM0001.MP3`) now takes it from the category of its parent folder: files in `vyasa puja 2015` become `2015-MM-DD_KKS_Vyasa-puja.mp3` (then `-2`, `-3` for files with the same name). Not used for the scripture categories (SB, BG, CC), which need a verse.
- New **Renamer** block on the Settings page with two options, for processing the archive automatically:
  - **Move files instead of copying** (off by default): the file is moved to processed-files and no longer stays in the archive. A move across disks (the NAS to this computer) is copied, read back and compared before the original is removed; if anything fails the original stays, and nothing is overwritten. Reject and Exclude put a moved file back where it came from; the Settings Reset will not delete processed copies that are the only copy.
  - **Add postfix to processed filenames** (empty by default, for example `_processed`): after a copy, the original left behind is renamed with the postfix (`2008-01-04_processed.mp3`) so processed files are easy to see. The new copy keeps its clean name; the postfix is not used when moving. Reject gives the original its name back.
- Byte-identical copies of a file (found by a scan) now go to their own folder `identical-files/` (next to `processed-files/`, not inside it) when the Renamer runs on the file they duplicate: copied, or moved when *Move files instead of copying* is on. They keep their own name (`-2`, `-3` on a clash), are handled once, and Reject/Exclude put moved copies back. Files without a WHAT now go to `unprocessed-files/`, also next to `processed-files/` (was `processed-files/_unresolved/`; copies already there are not moved automatically). New `.env` settings `MEDIA_UNPROCESSED_FILES_PATH` and `MEDIA_IDENTICAL_FILES_PATH`; Reset, Delete and `mam doctor` cover all three folders.
- `unprocessed-files/` and `identical-files/` keep the folders the original sat in, below its import root (`2012/Radhadesh Jan 2012/`), because the folder names are clues for sorting the files later.

### Class discoverer
- The result now says where the time went: `Took 4 min 12 s (volume 3 s, transcription 3 min 58 s, verse check 11 s)`, also stored per step in the job result. First measure on the Mac: a 100 minute recording takes about 3 minutes (about 31 times real time).
- New tool: evens out the volume of the class (-18 LUFS, only when needed), transcribes it (mlx-whisper on Apple Silicon, whisper.cpp elsewhere; the transcript is cached and written to `transcriptions/`), finds the verse the speaker announces and confirms it on vedabase.io (verse pages cached for good, 10 s between requests). In the Action menu and as `mam class-discover <file>`.
- A confirmed verse that the filename lacks is given to the Renamer at once (and to the video's row when the class came from a video); a different verse than the filename's goes to review with both candidates and an excerpt; so do unconfirmed verses and files with no verse and no category. 5% spot checks. Detected languages go to the `language` field. No title (the content-discoverer adds it) and no trimming.
- `uv sync` now installs mlx-whisper on Apple Silicon Macs (it brings torch, a large download). New optional `.env` settings: `TRANSCRIBER_BACKEND`, `WHISPER_MLX_MODEL`, `WHISPER_CLI`, `WHISPER_CPP_MODEL`, `WHISPER_DEVICE`, `TRANSCRIBE_WORDS`, `VEDABASE_URL`, `VEDABASE_DELAY_S`. The unused `vedabase_verse` table (migration 0004) stays empty: verse pages are cached in `vedabase_cache`.

### Kirtan splitter
- Fix: a recording with no kirtan (speech from the start) now continues to the class discoverer in the pipeline instead of stopping.
- New tool: finds where the kirtan ends in a recording that is kirtan followed by a class, from the audio alone (a local voice-activity model for the speech, loudness and treble for where the music stops, and silences; no transcription), and splits it into a kirtan file (named by mantra type) and the class. Runs after the Renamer; in the Action menu and as `mam kirtan-split <file>` (dry run by default, prints a timeline).
- Music throughout or no kirtan: not split. Unsure cuts open a review with a page to edit the time; sure ones (the music clearly stops, then speech; a kirtan of 1.5 to 35 minutes) are cut automatically, with 5% spot checks. The score is shown but no longer decides.
- A long kirtan followed by less than 3 minutes of talk (closing words) is a kirtan recording and is not split.
- Faster: the audio is decoded once instead of three times (about 30% less time per file, same results), and several files are analysed at the same time on the CPU pool (up to 8 workers, one per core minus 2).
- Class recordings finish sooner: the analysis stops once the class has lasted 5.5 minutes after the kirtan, because nothing later can change the cut. The decision looks at the first 5 minutes of the class only. Recordings that are not clear-cut are still analysed in full.
- Waveform editor on the review page: the kirtan and the class as two colored regions; drag the edge between them, nudge it by 0.1 or 1 s, cut at the playhead or type the time, and listen 3 s before, around or after the cut. A long recording opens after a one-time preparation (peaks and, for WMA and video, a small mp3 preview, kept in the work folder).
- Fix: a class that opens with a sung mantra is no longer cut 40 to 55 minutes in. The kirtan before a class must be a stretch of at least 90 s that is mostly music (70%), not music seconds scattered through a long class. A short sung opening (up to 90 s) belongs to the class. A recording of bhajans that begins with an announcement, or has talks in between with music after them, is a kirtan recording. The review reasons now say what was found at the start.
- Times are written as `hh:mm:ss.mmm` everywhere (review reasons, the cut field, the waveform header), never as plain seconds. The waveform shows the time under the mouse.
- A random check (5% of the sure decisions, `SPOT_CHECK_RATE`) now says so in plain words: nothing is wrong, nothing was changed.
- A recording of bhajans with talks in between and 10 minutes or more of music after the last talk is a kirtan recording, skipped without a review; with 5 to 10 minutes it is proposed and checked.
- Video recordings: the audio is extracted as mp3 first.
- Common phrases are ignored for now (they need a transcript).

### Media manager
- New **Delete**: "Delete (to the Trash)" in the Files Action menu (selected or all matching files, with a confirmation) and a **Delete** button on the file details page. The copy in processed-files is moved to the Trash (macOS Trash, Windows Recycle Bin), so it can be put back by hand. The original on the NAS and the Baserow row are never touched. The file stays in the list with the status `Deleted` and is not processed again; an open review on it is closed.
- **Exclude** now cleans up: the original stays where it was imported, and a copy the Renamer already made in processed-files is moved to the Trash (the button warns first). The file stays in the list as `Excluded` and is not processed again.
- Details page tidied up: the waveform is always shown above the form (it replaces the old "Listen" player and is the same player as the Kirtan splitter's, which adds the cut when a review is open); the form is set apart in its own highlighted block; input fields now stand out from their card in dark mode; the Renamer proposal, derived files, jobs and history are in one collapsed section under the page. The splitter evidence shows its times as `hh:mm:ss.mmm`.
- Material Design 3 in light mode too, on every page: M3 colors, pill-shaped buttons, rounded cards, filled input fields, M3 colors for the waveform.
- Icons instead of words where the meaning is clear (preview, run, next review, waveform play/nudge/zoom controls, upload, cancel queued, reset filters), each with a tooltip. The "Preparing the waveform" sentence and the "Loading…" text are now just a spinning icon.
- Dark mode follows Material Design 3 (baseline dark colors: surfaces, text, outlines, primary and error colors) with higher contrast. Placeholder text is readable, and the form card header is a neutral surface instead of dark blue. Explanations of buttons and fields are tooltips (a (?) icon next to the label, or hover on the button) instead of text on the page; light mode is unchanged.
- Approving a review in a pipeline run now says that the pipeline goes on ("The pipeline continues by itself: Kirtan splitter is queued for this file"), with the spinner until the job has finished.
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
