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
  - browse and import NAS folders
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
- **Travel schedule:** evidence only, never a source on its own.
  - A date inside the exact `[start_date, end_date]` range supports the place, and the reverse.
  - It fills WHERE only when the filename has no place.
  - It never overrides the filename or the folder.
- **The Renamer flags a file for review when:**
  - the filename is non-descriptive
  - the date format cannot be determined, even with the travel schedule and the media table
  - a new place or country is needed. The review page then offers a one-click add to `travel_schedule` (and to the select option), applied only after confirmation.
- **The Renamer also flags a file when:**
  - the filename names another speaker (e.g. `SNS`, `BVS`) and not KKS, since WHO is always KKS
  - the country code contradicts a known place (`Oslo_fi`) and cannot be corrected safely
  - a different file already has the final name (it is saved with `-2`)
- **Missing WHAT:** the file goes to `_unresolved/`, and later tools may fill in the WHAT.
- **Dates:** European day-month-year is the default reading; other orders need supporting evidence. Dates in the ID3 title or date tag are taken as correct unless other evidence conflicts (a conflict with the folder name is flagged). See docs/references/filenaming-convention.md.
- **More evidence used:**
  - sibling files in the same folder decide ambiguous dates (`08-04-10` among April 2008 files is 2008-04-10) and give the place of files recorded the same day
  - the travel schedule only suggests places in the country the filename names; every stay on the date is shown as evidence, and on a travel day (leaving one place, arriving in another) the place is left open
  - a new place flag suggests the closest known place (`Fruska-Goranga`: did you mean `Fruska-Gora`?)
  - seed aliases (place spellings such as `Praha`, `Koln`, `Rdesh`; category words such as `Kirtany`; language words such as `GER`) are in `mam/tools/renamer/aliases.yaml`
- **Rule-based:** the Renamer does not call the LLM; unusual names are flagged instead.
- **Every run is copied:** a flagged file is still copied and renamed (into its category folder or `_unresolved/`), so the review edits a real copy. Running the Renamer again on a processed file moves that copy; it never makes a second one.
- **Dry run:** shows the proposed name in the file list without copying files, changing the status or writing to Baserow. It never opens a review; the file page says so and offers to run the Renamer for real.

### Kirtan-splitter
- **Video first:** extracts the audio as mp3 in its original quality, with a name that matches the video.
- **Boundary detection:** finds where the kirtan starts and ends using:
  - the shared transcript (transcribed once per content hash and reused by the class-discoverer)
  - music/speech classification
  - common phrases
- **No split:** a recording that is singing throughout is not split.
- **Cutting:** by stream copy. Kirtan files are named by mantra type, as in its SPECS.md.
- **Automation:** cuts automatically when every boundary scores above the learned threshold (see section 10). Otherwise the file goes to `Kirtan-splitter Review`.

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
- **Live writes:** tools write to Baserow as they go, against the live database (there is no test database).
- **Safeguards:**
  - Baserow writes are dry-run by default and are sent only with an explicit `--apply` or the manager's apply action.
  - Every write is logged in the local DB with before and after values, so it can be undone.
- **When tools write:** the Renamer writes a file's row when its name is final: right away for an unflagged file, and after approval for a flagged one.
- **Formats:**
  - `audio_file_location` / `video_file_location`: the path below the processed-files root, e.g. `/Srimad-bhagavatam/2011-08-28_KKS_SB-1-19-30_Oslo-no.wma`
  - `tag`: verse numbers only, as existing rows have them (`3.6.6`; CC with its lila: `Madhya 20.154`)
  - an existing row is matched by date plus verse tag, or by date plus category and place; otherwise a new row is created
- **Overwrite policy:**
  - Tools only fill empty fields and never overwrite values a person entered. Differences are shown as proposals on the review page.
  - `notes` gets a dated entry appended for each tool.
  - `language` is merged (union).
- **Schema:**
  - Only existing columns and select options are used. The schema is refreshed at runtime.
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
- **File list:**
  - Opens on **Needs attention** (files waiting for a review, failed or corrupted); *All files*, *Needs review*, *Failed* and each single status (with counts) are one choice away. A short guide on the page explains every status.
  - Sorted by path, files are grouped under a header row per folder (shown relative to its import root, e.g. `Sample files / 2012 / Radhadesh Jan 2012`) with alternating row colors.
  - An action without a status filter applies to all files.
  - Columns: original path and name, new name, status, date, title, location.
  - Search, filter and sort; select files one by one or all at once.
  - The action menu lists each tool plus "Run full pipeline", with two buttons: **Preview (dry run)** and **Run for real**.
  - The list refreshes itself while any job is queued or running.
- **File page:**
  - The run buttons are at the top, with a "Last run" line showing the latest job's result and an "Open the review" button when a review is waiting.
  - While a job for the file is queued or running, the page shows it and reloads itself when the job finishes.
  - Times are shown in the computer's own time zone.
- **Review detail page:**
  - Edit each W (the date allows placeholders), with a live filename preview validated against the convention.
  - See the evidence for each field and pick from conflicting candidates. Each item says where it was found: the filename, a folder name, the embedded ID3 tags (with the tag text), other files in the folder, the Baserow media table, the travel schedule or a naming rule.
  - Link the file to an existing Baserow row (dedupe) or create a new row, and edit its Baserow fields.
  - When Baserow is in dry-run mode the page says so, and after approving the message says the Baserow row was logged but not written.
  - Buttons: Edit/Save, Approve, **Reject result** (discards the output, reverts through the history and re-queues with a note), **Exclude file** (stops all processing).
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
- None at the moment. Add new ones here instead of inventing an answer.
