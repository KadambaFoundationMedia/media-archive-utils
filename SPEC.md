# Project Specification

This document records the cross-cutting decisions for the Media Archive Tools project. It is
authoritative where it conflicts with older wording in the per-tool or per-helper SPECS.md files.
Items under "Open decisions" are not decided: ask instead of guessing.

## Table of contents
1. Stack and runtime
2. Files, identity and storage
3. Filename rules
4. Processing pipeline and tools
5. Transcripts
6. Baserow
7. File statuses
8. Media manager UI
9. Testing and development safety
10. Confidence and learning
11. Open decisions

## 1. Stack and runtime
- **Language:** Python, managed with `uv`.
- **Media manager:** FastAPI backend, Jinja2 templates, Bootstrap 5, HTMX for live updates, Alpine.js for small interactions, Font Awesome Free icons, wavesurfer.js (regions plugin) for the waveform editor. No JavaScript build step.
- **Versioning:** one project version (`mam/__init__.py`, semver), shown by `mam --version` and in the portal footer. CHANGELOG.md lists changes per component. Versioning started on 2026-11-01, when the media manager and Renamer were approved (1.0.0).
- **CLI:** a single entry point `mam`:
  - `mam serve`: start the media manager
  - `mam <tool> [--dry-run] <path>`: run one tool on a file or folder
  - `mam scan`: index the archive
  - `mam doctor`: check ffmpeg/ffprobe, the transcription backend and models, `.env`, and whether Baserow can be reached
- **Install:** `uv sync`. External binaries (ffmpeg, ffprobe, whisper-cli) come from Homebrew on macOS and winget on Windows.
- **Transcription:**
  - macOS (Apple Silicon): `mlx-whisper` with the large-v3-turbo model
  - Windows: whisper.cpp `whisper-cli` with the GGML large-v3-turbo model
  - Both backends sit behind one transcriber interface. Each run records engine, version, model hash, backend and any fallback (see ARCHITECTURE.md).
- **Local database:** SQLite on the local disk, never on the NAS share. Export and import use the SQLite file plus a JSON/CSV dump command.
- **LLM:** a pluggable provider interface. The default is the Claude API (latest model, key in `.env`); a local model is optional. Results are cached by (input hash, prompt version) so re-runs are not billed again.
  - The Renamer is deterministic (date parsing, regexes, lookups). It uses the LLM only as a fallback for unusual or foreign-language folder and file names.
- **Speaker separation:** lightweight speaker embeddings (for example SpeechBrain ECAPA) are compared against a KKS voice profile built from the sample files (see section 10). A segment that is "not KKS" is an audience candidate.
- **Vedabase:** verse pages are fetched on demand and cached in the local DB indefinitely. Requests are limited to 1 per second and send a clear User-Agent.
- **Concurrency:** a job queue persisted in SQLite, with a worker pool per resource. Defaults: 1 ML worker (transcription, embeddings), a few ffmpeg/CPU workers, and I/O workers for copies and Baserow. All sizes are configurable in `.env`. The queue survives restarts, so the Renamer can process hundreds of files while ML work runs one file at a time.
- **Users:** one operator on localhost. The server binds to 127.0.0.1 with no authentication. The operator name from config is recorded on every approval.

## 2. Files, identity and storage
- **Originals are read-only forever.** Tools never rename, move, modify or delete an original; every tool works on copies.
- **NAS access:** through a mounted share (root path in `.env`). Files are read directly from the mount, temporary and intermediate files go to local disk, and final outputs are written to the processed-files root (path in `.env`).
- **Identity:**
  - At scan time each file gets a quick fingerprint: size, mtime, and an xxhash of the first, middle and last 4 MB.
  - A full SHA-256 is computed lazily, when the file is processed or when two fingerprints collide.
  - Derived files (kirtan/class split, trimmed, optimized) get their own IDs, linked to their source with `parent_id`.
- **Scope:** only audio and video extensions are scanned. Everything else (photos, zips, ebooks, design files) is ignored.
- **Copying into processed-files:**
  - The Renamer copies the file to `processed/<category-folder>/` immediately, and later tools write new versions there.
  - The category folder is found by matching the WHAT against the Baserow `category_terms_folder` table.
  - With no WHAT the file goes to `processed/_unresolved/`. It moves to its category folder once a later tool or a reviewer finds the WHAT; the history is kept.
- **Name clashes:**
  - Same final name, different content: append `-2`, `-3` before the extension and flag the file for review.
  - Same name and same size (a duplicate): copy it with a `_duplicate` suffix, as the file-organizer spec requires.
- **After a kirtan/class split:** the combined processed copy is discarded and the original stays on the NAS. The combined file's Baserow row becomes the class row, and the kirtan gets a new row.
- **Ingest:** four ways to add files:
  - browse and import NAS folders (the Import page lists the subfolders first, then the media files directly in the browsed folder with their size; other file types are not listed). Each folder and file has a checkbox (plus select all); **Import** (left) imports only the ticked ones (a ticked folder with all its subfolders, a ticked file alone), and **Import all** (right) imports the whole browsed folder with its subfolders). The top bar has an **Add media** button (on every page) that opens the Import page
  - a full archive scan (index only, no processing)
  - drag-and-drop upload into an inbox folder
  - a watched inbox folder, whose new files are registered automatically

## 3. Filename rules
These clarify docs/references/filenaming-convention.md.
- WHO is always `KKS`.
- A file is always renamed with whatever W's are known. For example, a file with only a date becomes `YYYY-MM-DD_KKS.mp3`.
- Date parts may use the `YYYY`, `MM` and `DD` placeholders. WHO, WHAT and WHERE never use placeholders; a missing segment is left out.
- WHERE takes one of three forms:
  - `Place-cc`: place in Title-case, ccTLD in lowercase
  - `cc`: country only
  - omitted, when nothing is known
- Recordings from the same day are told apart by their WHAT. Only a full collision gets a `-2` suffix.
- WHAT is the verse plus the short title from the content-discoverer, for example `SB-9-20-20-Dont-waste-time`. The content-discoverer title replaces the class-discoverer title.
- The whole filename stays between 25 and 128 characters.

## 4. Processing pipeline and tools
Pipeline order: **renamer → kirtan-splitter → class-discoverer → content-discoverer → class-trimmer**.

- Each tool can run on its own (one file or a batch) or as part of a pipeline coordinated by the media manager.
- In a batch, a flagged file pauses on its own while the other files continue. After approval it resumes automatically at the next step.
- Kirtan-only outputs are `Skipped` for steps 3 to 5.
- **Video:** the pipeline is audio-only. Video files are renamed and copied but never edited.
- **Cutting:** splits and trims use stream copy in the original codec (no re-encode), because most sources are already lossy. Gain changes need a re-encode, written at the source codec, bitrate, sample rate and channel count.
- **Loudness method:** loudness is measured with ffmpeg `ebur128`. When the peaks allow it, the gain is a pure linear change. When they don't, the gain is followed by a peak limiter running at 4× the sample rate (so inter-sample peaks are caught), with one corrective pass if limiting cost loudness. This was chosen over a two-pass `loudnorm` because it is about 7× faster with the same result. Files that needed the limiter are marked `dynamic` so they can be flagged.

### Renamer
- **Evidence order:** filename > parent folder names > ID3/tags (medium to high confidence) > Baserow media table > travel schedule. Each W is resolved on its own.
- **Travel schedule:** evidence only, never a source on its own: it says where KKS possibly was on a date. It never gives a place its country and is never written by the portal.
  - A date inside the exact `[start_date, end_date]` range supports the place, and the reverse.
  - It fills WHERE only when the filename has no place.
  - It never overrides the filename or the folder.
- **The Renamer flags a file for review when:**
  - the filename is non-descriptive
  - the date format cannot be determined, even with the travel schedule and the media table
  - a new place or country is needed. The flag reads "New place 'X' is not in Baserow. Add it as a place_location option in Baserow, or correct it" (never the travel schedule, which is not written); saving the file with that place and its country adds it to `place_country`.
- **The Renamer also flags a file when:**
  - the filename names another speaker (e.g. `SNS`, `BVS`) and not KKS, since WHO is always KKS
  - the country code contradicts a known place (`Oslo_fi`) and cannot be corrected safely
  - a different file already has the final name (it is saved with `-2`)
- **Missing WHAT:** the file goes to `_unresolved/`, and later tools may fill in the WHAT.
- **Dates:** European day-month-year is the default reading; other orders need supporting evidence. Dates in the ID3 title or date tag are taken as correct unless other evidence conflicts (a conflict with the folder name is flagged). See docs/references/filenaming-convention.md.
- **More evidence used:**
  - sibling files in the same folder decide ambiguous dates (`08-04-10` among April 2008 files is 2008-04-10) and give the place of files recorded the same day
  - when nothing else gives the country, the places named by the other files in the folder do, if they are all in one country (`KKS DUBEN 2008 MP3/02 KKS. SB. 3.1.20.mp3` gets `cz` because `03 BRNO LEKCE…` is in the same folder); the city stays open, and places in two countries give no country
  - the travel schedule only suggests places in the country the filename names; every stay on the date is shown as evidence, and on a travel day (leaving one place, arriving in another) the place is left open
  - a new place flag suggests the closest known place (`Fruska-Goranga`: did you mean `Fruska-Gora`?)
  - seed aliases (place spellings such as `Praha`, `Koln`, `Rdesh`; category words such as `Kirtany`; language words such as `GER`) are in `mam/tools/renamer/aliases.yaml`
- **Summercamp:** a filename (or a folder name) with `summercamp` in it (`Serbia-Summercamp`, `Swiss summercamp`, `Summer-camp`) gets the `Summercamp` category, unless the filename has a book and verse; then the book's category (`SB-1-2-3` gives Srimad-bhagavatam). Only an existing Baserow category option is used.
- **Rule-based:** the Renamer does not call the LLM; unusual names are flagged instead.
- **Confirmed values:** what a person saves on the file page or approves in a review is stored as *confirmed*. A later Renamer run keeps confirmed values and only fills what is still empty (or completes a date with placeholders). Other tools pass what they discover as evidence (e.g. the class-discoverer's verse); when it contradicts a confirmed value the file is flagged for review instead of being overwritten.
- **Every run is copied:** a flagged file is still copied and renamed (into its category folder or `_unresolved/`), so the review edits a real copy. Running the Renamer again on a processed file moves that copy; it never makes a second one.
- **Dry run:** shows the proposed name in the file list without copying files, changing the status or writing to Baserow. It never opens a review; the file page says so and offers to run the Renamer for real.

### Kirtan-splitter
- **No transcription.** The splitter decides from the audio alone, with local processing. Transcription belongs to the class-discoverer. **Common phrases are ignored for now** (they need a transcript, which does not exist when the splitter runs); the instruction returns when a transcript is available.
- **Video first:** extracts the audio as mp3 in its original quality (an mp3 stream is copied, other codecs are encoded once at their own bitrate), with a name that matches the video. The video copy stays as it is; the class audio becomes its own row, a child of the video.
- **Needs the Renamer first:** a real run cuts the renamed copy in processed-files. A dry run (`mam kirtan-split <file>`) may read any file and writes nothing; it prints a timeline of what was heard (`m` music, `s` speech, `.` silence, one character per 10 s).
- **Boundary detection** (`mam/tools/kirtan_splitter/`): every second of the recording gets the probability that someone is speaking, from the Silero voice-activity model (MIT licence, a 2.3 MB ONNX file in the repository, run locally with onnxruntime on macOS and Windows; nothing is downloaded). Singing with instruments scores near 0 and a class well above 0.10 (on the five trimming samples: kirtan 0.00 to 0.01, class 0.41 to 0.65 on average). The score is averaged over 9 s.
  - The class starts at the first run of at least 20 s of speech that has at least 90 s of music before it. The kirtan ends at the first second of that run that really speaks.
  - The cut is moved into a silence (ffmpeg `silencedetect`, -40 dB, 0.8 s) within 3 s, and silence at the very start is trimmed.
  - Why not simpler audio measures: pauses, zero-crossing rate and syllable rhythm were tried first and found the right cut in only one of the five samples (2026-11-01).
- **No split:** music throughout is a kirtan recording; speech with no kirtan of at least 90 s before it is a class. Both are `Skipped` when sure, and go to review when not.
- **Cutting:** by stream copy, one cut at the end of the kirtan (the stretch between the kirtan and the first speech stays at the start of the class, for the class-trimmer). The kirtan keeps the recording's date and place and takes the mantra as its WHAT. The mantra comes from the class's WHAT, as SPECS.md says: SB or BG gives `Jaya-radha-madhava`, CC gives `Jaya-Jaya-Sri-Caitanya`; anything else is named `Kirtan` and the reviewer can pick another type (including `Nrsimha-pranama`). It goes to the folder of that WHAT. The class keeps the combined copy's name, place and row (the combined copy is replaced by the class; the original on the NAS is never touched). The source extension is kept (`.wma` stays `.wma`).
- **Names are built by the splitter**, from the Renamer's saved proposal, because the derived kirtan has no original path for the Renamer to read. The kirtan has its own local record (`derivation = kirtan`, `parent_id` = the recording, status `Processing complete`) and a new Baserow media row written with the Renamer's switch rules; the class keeps its row.
- **Automation:** cuts automatically when the score is at or above the threshold (0.9 until calibrated, then the learned one; section 10). Below it, or for a 5% spot check (`SPOT_CHECK_RATE`), nothing is cut and the file goes to `Kirtan-splitter Review`. The score adds: 0.35 for a found boundary, up to 0.25 for the contrast between the speech probability after and before the cut, 0.15 for a cut in a silence, up to 0.10 for how purely music the kirtan is, 0.10 for a kirtan of 3 to 35 minutes (minus 0.20 outside that), 0.05 when the class tells the mantra.
- **Review page:** shows the reasons, the timeline and evidence, plays the recording from 3 s before the cut, and takes the cut as `minutes:seconds` and the kirtan type. Buttons: **Approve and split**, **Do not split**, **Reject result**, **Exclude file**. The cut is made only on approval. An approved cut within 1.5 s of the proposal counts as approved, otherwise as edited; both are labeled examples for the threshold. The waveform editor (section 8) replaces the text field in a later release.

### Class-discoverer
- **Whole-recording gain:** runs the audio-optimizer and writes the result to the processed copy (encode #1). The kirtan-splitter has already separated any kirtan, so the gain is measured on the class alone and loud singing cannot skew it.
- **Verse discovery:** transcribes the recording, determines the scripture and verse, and confirms them on vedabase.io.
- **Filename precedence:** if the transcript clearly shows a different verse than the filename, the name is kept and the file is flagged for review. The review shows both candidates with a transcript excerpt.
- **Language:** transcribes as spoken, in any language. Detected languages are merged into the Baserow `language` field.

### Content-discoverer
- 1 to 5 topics, each at most 2 words and not too general. They are written to the Baserow `themes` column as one text value separated by a comma and a space (`Krishna Prema, Kali Yuga`), the format the existing rows already use.
- A YouTube description in English, 2 to 3 paragraphs, always ending with the footer (current year).
- A short English title, which goes into the WHAT.
- All results are written to Baserow: the description to `media_description` (and `youtube_desc` set to true), per helpers/baserow/SPECS.md.

### Class-trimmer
- **Sections:** detects them per docs/references/editing-standard.md and cuts by stream copy.
- **Audio-optimizer pass:** then calls the audio-optimizer for audience-question gain and a final loudness pass (encode #2):
  - target −18 LUFS ±1 LU, ceiling −1 dBTP
  - a required gain above 12 dB always goes to review
- **Non-verse recordings** (festivals, seminars, workshops): trims only the leading and trailing ambience and the intro mantra, applies the Q&A gain and the final pass, and flags the file if the structure is unclear.
- **Automation:** cuts automatically when every boundary scores above the learned threshold (see section 10). Otherwise the file goes to `Class-trimmer Review`.

## 5. Transcripts
- **Files:** `transcriptions/<final-base-name>.md`, Markdown with a `[hh:mm:ss.mmm]` timestamp on each segment. The transcript is renamed whenever its audio file is renamed.
- **Database:** full segment- and word-level data, plus the binding metadata, lives in the local DB for the tools and the waveform editor.

## 6. Baserow
- **Live writes:** tools write to the live Baserow database (there is no test database).
- **Safeguards:**
  - A **Baserow writes** switch in the portal's top bar (on every page; ON shows in red) decides whether runs write to Baserow. It is stored in the local DB, takes effect at once, defaults to **off**, asks for confirmation when switched on, and a reset turns it off again. While it is off, runs only log the rows they would write.
  - Every write is logged in the local DB with before and after values, so it can be undone.
- **When tools write:** the Renamer writes a file's row when its name is final: right away for an unflagged file, and after approval for a flagged one.
- **Live or logged:** runs (**Run** on one or many files, the pipeline) follow the switch. **Approving a review always writes live**, because it is an explicit human decision; the review page says so.
- **Matching an existing row:** the date alone never decides. Among the rows on the same date, a row matches only when it also agrees on the verse `tag` and/or the `title` (shared title words, ignoring stop words, places and countries), with `place_location`/`country` and category as further evidence. A different verse, place or country counts against the row; an online place (`Zoom-livestream`) is neutral, because a livestream happens somewhere. When several rows fit, the file is flagged so the reviewer picks the row; otherwise a new row is created.
- **Formats:**
  - `audio_file_location` / `video_file_location`: the path below the processed-files root, e.g. `/Srimad-bhagavatam/2011-08-28_KKS_SB-1-19-30_Oslo-no.wma`
  - `tag`: verse numbers only, as existing rows have them (`3.6.6`; CC with its lila: `Madhya 20.154`)
- **Overwrite policy:**
  - Tools only fill empty fields and never overwrite values a person entered. Differences are shown as proposals on the review page.
  - `notes` gets a dated entry appended for each tool.
  - `language` is merged (union).
- **Reference data cache:** the categories (with their colors), places, travel schedule and media index are read from Baserow and kept in memory and on disk (`~/.mam/cache/baserow_reference.json`). Pages use the cached copy at once; data older than 10 minutes is refreshed in the background, so nothing waits for Baserow. Work that files a recording (Renamer jobs and saving an edit) fetches fresh data first when the cached copy is over 60 seconds old, so a search term just added to `category_terms_folder` counts; if Baserow is unreachable it falls back to the cached copy. Saving a file with a new place and country refreshes it straight away.
- **Schema:**
  - Only existing columns and select options are used. The schema is refreshed at runtime.
- **Place and country:** the `place_country` table (one row per media `place_location` option: `place_location`, `country`, `ccTLD`) is the only source of a place's country. General places (Zoom-livestream, Home-program, Summercamp, Govindas; `no_country` in `mam/tools/renamer/aliases.yaml`) have no country: the file's own evidence decides. It was filled once with `uv run mam place-country sync`, which also gave media rows with a place but no country that place's country and unified the `Czech-republic`/`Czech-Republic` spelling. A person's save adds a missing row or fills an empty country, live; a row with another country is never changed (the file page warns at the Country field).
  - The `place_country` table also has an `Aliases` column (read ignoring case), filled by hand: other spellings of the place separated by semicolons (`ISKCON-Korsnäs-Gård` has `New-Radhakunda-Dham;NRK`). The Renamer reads them with the table and treats an alias like the place itself, so the filename gets the canonical place and its country. An alias written by hand wins over a real place of the same name and over the seed aliases (`Fruska-Gora` listed on `Summercamp` makes `Fruska-Gora` mean `Summercamp`); when two rows list the same alias the first row keeps it. An alias can point at a place that `not_places` hides by its own name (`Summercamp`: the bare word is not a place, its listed aliases are). The portal never writes `Aliases`.
  - The `users` and `media_review_tasks` tables are ignored.

## 7. File statuses
- `Not processed`
- `Queued <tool>`
- `Processing <tool>`
- `<Tool> Done`: finished this step, waiting for the next one
- `<Tool> Review`: needs a person's judgement
- `<Tool> Failed`: an error such as a network problem, Baserow being down or an ffmpeg failure; can be retried
- `Skipped`: the step does not apply, for example splitting a kirtan-only recording
- `Excluded`: the reviewer stopped all processing of this file
- `Corrupted`: the media cannot be read
- `Processing complete`

## 8. Media manager UI
- **General:** Bootstrap with Font Awesome, a dark/light switch, and a mobile-friendly layout.
- **Live updates:** while jobs are queued or running, the navbar counts (Files, Review, Jobs) update every 2 seconds on every page and stop when the queue is empty. A message about queued work (an import, "Renamer: 10 file(s) queued") shows a spinner and turns into "Finished" when the jobs are done. Messages are shown once: a reload does not bring them back.
- **File list:**
  - Opens on the **default Status filter** chosen on the Settings page (*All files* until chosen). The Status choices are *All files*, *Review* (a `<Tool> Review` status: a tool needs your decision), *Failed* and each single status (with counts). There is no separate "Needs attention" or "Needs review" view; the old `status=__attention` URL maps to *Review*. Under *One status* every status of section 7 is listed, also when no file has it (count 0), with the per-tool statuses for each tool that is built (`Queued Renamer`, `Renamer Review`, ...). The guide on the page explains the views and the statuses in the same order as the list.
  - Sorted by path, files are grouped under a header row per folder (shown relative to its import root, e.g. `Sample files / 2012 / Radhadesh Jan 2012`) with alternating row colors.
  - An action without a status filter applies to all files.
  - Every filter has an always-visible × that clears only that filter (dimmed when there is nothing to clear). Clearing Status shows all files; **Reset all** returns every filter to its default (the Settings default Status, sorted by path).
  - **Category** and the search use what a Renamer dry run suggested too, until the file is saved: the Category filter lists the saved categories and the suggested ones, and filtering and sorting by Category match the category shown in the column. Search also looks in the suggested name, WHAT and WHERE.
  - The Title / WHAT column is plain text. The **Category** column shows the category as a pill in its color from the Baserow `category_terms_folder.color` column (e.g. Bhagavad-gita blue, Srimad-bhagavatam light-red). The top bar (with the **Add media** button, right of the Settings link) stays fixed; the filters scroll away, and once they are out of view the table header row sticks just below the top bar. File names use the normal text color; finished tool steps are green.
  - Columns: original path and name, new name, status, date, title, location.
  - Search, filter and sort; select files one by one or all shown at once (the select-all box also ticks files added by Load more).
  - **No page numbers.** A **Show** select next to the filters (50, 100, 200 or 500; default 50, kept in the URL as `size`) sets how many files load at first. When more files match, the table ends with "Showing X of Y files" and a **Load more** button that appends the next batch of that size (the last batch may be smaller) below the rows already shown. Filters, sort and the sticky header keep working; changing a filter or the Show size starts again from the top. The folder header rows continue across batches (no repeated header). The self-refresh while jobs run keeps all loaded rows.
  - The action menu lists only the pipeline tools that are built (currently the Renamer and the Kirtan splitter) plus "Run full pipeline"; helper functions such as the audio-optimizer's verify and measure are not menu actions (they stay available as `mam audio ...` commands), with two buttons: **Preview (dry run)** and **Run**. The result line of a run says what happened in Baserow (row written, logged only, or waiting for the review).
  - The list refreshes itself while any job is queued or running.
- **File page:**
  - One form for the file's **filename** and its **media table fields** (Date, Title, Category, Tag, Place, Country, Language, Notes, and which Baserow row to link; the note is kept with the file and added to the Baserow `notes` column with the person's name). They stay in step: editing the filename fills the fields from it; changing Date, Place or Country rewrites that part of the filename. Problems (e.g. a date that is not YYYY-MM-DD) block saving.
  - **Place is a list of the Baserow `place_location` options**, like Category and Country, so only places Baserow knows can be chosen. A place that comes from a filename and is not an option (e.g. `Fruska-Goranga`) is shown as "(not in Baserow)" with a red warning at the field and a suggestion ("did you mean Fruska-Gora?"); it is used in the filename but `place_location` stays empty (and an old value is cleared). To use a new place, add the option in Baserow, then save again: saving refreshes the place list when a place is not known (or use **Refresh Baserow data** on the Settings page). A real Baserow option always beats a Renamer alias.
  - Choosing a place fills Country from `place_country`. Saving a place with a country that `place_country` does not have yet adds it there; choosing another country than `place_country` has shows a warning at the Country field (the media row gets the chosen country, `place_country` is not changed).
  - The portal cannot add or delete select options itself: the Baserow database token and the Baserow MCP connection only allow row actions (create, read, update, delete rows), not field changes. Options are added in Baserow.
  - **Save** renames the copy in processed-files, writes the media row live, confirms the values and closes any open review. The review page uses the same form, with Approve / Reject result / Exclude file.
  - The run buttons are at the top, with a "Last run" line showing the latest job's result and an "Open the review" button when a review is waiting.
  - While a job for the file is queued or running, the page shows it and reloads itself when the job finishes.
  - Times are shown in the computer's own time zone.
- **Review detail page:**
  - Edit each W (the date allows placeholders), with a live filename preview validated against the convention.
  - See the evidence for each field and pick from conflicting candidates. Each item says where it was found: the filename, a folder name, the embedded ID3 tags (with the tag text), other files in the folder, the Baserow media table, the travel schedule or a naming rule.
  - Link the file to an existing Baserow row (dedupe) or create a new row, and edit its Baserow fields.
  - When Baserow is in dry-run mode the page says so, and after approving the message says the Baserow row was logged but not written.
  - Buttons: Edit/Save, Approve, **Reject result** (discards the output, reverts through the history and re-queues with a note), **Exclude file** (stops all processing).
- **Settings page:**
  - Shows the current settings from `.env` (read-only).
  - **Default Status filter:** a select with the same choices as the file list's Status filter; the file list (and **Reset all**) opens on it. Stored in the local database (`app_setting` key `default_status`), All files until chosen.
  - **Place → country:** how many places `place_country` has, the place options without a country (general places excluded), and rows that cannot be used (a place or country Baserow does not know, a place with two rows).
  - **Reset the media manager** for a clean start: clears all file records, jobs, reviews, evidence, history and the Baserow write log, and (optionally, on by default) deletes the copies in processed-files. Learned corrections and caches are kept unless chosen.
  - A full database backup is made first (`~/.mam/backups`, restore with `mam db import`). Originals and Baserow are never changed. It refuses while jobs run and needs `RESET` typed to confirm. The same reset is available as `uv run mam reset`.
- **Waveform editor** (for splitter and trimmer reviews):
  - Colored, labeled, draggable regions, each with a KEEP/TRIM toggle. Colors come from Baserow `category_terms_folder.color`.
  - Approve each boundary separately, then "Approve all".
  - Preview playback that skips trimmed parts, plus a ±3 s audition around each cut.
  - Approving tells the tool to apply the cuts.

## 9. Testing and development safety
- **Framework:** pytest.
- **Golden fixtures:** a YAML file lists each sample file with its expected name, split/trim timestamps (±1.5 s) and verse.
- **Test speed:** fast unit tests cover parsing and naming. Integration tests marked `slow` run real transcription on `trimming-samples/`.
- **Dev safety:** development runs use only `sample-files/`, and every tool and helper that changes files has a dry-run mode.

## 10. Confidence and learning
Thresholds are learned from labeled examples, not hard-coded. The goal is a balance: automate what the tools reliably get right and flag only what is genuinely uncertain.

### Labeled examples
- **Seed set:** the documented examples in the tool specs (the kirtan-splitter and class-trimmer timestamps, and the renamer and class-discoverer names) are stored in the golden fixtures.
- **Growing set:** every review outcome becomes a new labeled example. Approved unchanged confirms the tool, edited records the correction, and rejected marks a failure. Each is stored in the local DB with the tool's original proposal and its features.

### Confidence scores
- Each tool combines its evidence into one score per decision: a boundary for the splitter or trimmer, or a W for the renamer.
  - Splitter and trimmer evidence: common-phrase anchors, music/speech classifier margin, silence gap at the boundary, speaker-change evidence, and agreement between sources.
- Scores are calibrated against the labeled examples, so a score of 0.9 means about 90% of past decisions at that score were correct.

### Auto/review thresholds
- **Per tool:** each tool's threshold is the lowest score at which auto-applied decisions on the labeled set still reach the target precision. The default target is 95% of auto-applied cuts within ±1.5 s of the approved timestamp.
- **Below threshold:** the file is flagged for review.
- **Too few examples:** while a tool has fewer than 20 labeled examples, everything below a conservative starting threshold (0.9) goes to review.
- **Recalibration:** thresholds are recalculated automatically every 25 new review outcomes, and on demand with `mam calibrate`.
- **Spot checks:** a small share of auto-applied files (default 5%) is sent to review anyway, so the precision estimate stays honest.
- **Dashboard:** the media manager shows, per tool, the auto rate, the review rate and the measured precision. The target precision and spot-check rate are configurable in `.env`.

### Naming mismatches
The kirtan-splitter examples (`oslo-noi`, a kept `-2` index, `ZOOM0004` as a WHAT) show that real names are messy. The tools handle them with validation instead of fixed rules:
- **Validation:** each segment is checked against known values: ISO country codes, `travel_schedule` places, Baserow categories and verse patterns.
- **Auto-correct:** when exactly one valid correction exists, the tool applies it and records it in `notes`, for example `noi` → `no` (Norway, Oslo is in the travel schedule).
- **Review:** when no correction exists or several do, the file is flagged.
- **Meaningless parts dropped:** source indexes (`-2`) and device names (`ZOOM0004`, `R09_0003`) are removed from the new name, which follows the omit-unknown rule. The original name is always kept in the local DB and Baserow.
- **Learning:** corrections that reviewers make repeatedly (the same wrong token fixed the same way at least 3 times) are added as alias rules, so the same mistake is auto-corrected next time.

### KKS voice profile
- **Built from samples:** made from main-speaker segments of any file in `sample-files/` or `trimming-samples/`. The main speaker is the dominant speaker cluster in a class, confirmed by the class sections in the transcript.
- **Stored locally:** the profile is saved in the local DB.
- **Refreshed:** it is rebuilt when new approved recordings add coverage, such as other years or other microphones.

## 11. Open decisions
- **Video in the pipeline after the kirtan-splitter:** the splitter extracts the audio of a video and splits it into two audio rows (kirtan, class) under the video. How the class-discoverer, content-discoverer and class-trimmer continue for the class audio (and how the video row ends) is not decided; decide it before building the class-discoverer.
- **Common phrases for the splitter:** ignored until a transcript exists when the splitter runs (decided 2026-11-01).
- **Calibration:** the threshold is read from the `calibration` table (0.9 when empty), but `mam calibrate` and the automatic recalculation every 25 outcomes are not built yet. Review outcomes are already stored as labeled examples.
