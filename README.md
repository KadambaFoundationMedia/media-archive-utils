# Media Archive Utils

Tools for discovering, renaming and editing the Kadamba Kanana Swami media archive.
See [SPEC.md](SPEC.md) for the decisions behind the project and [CLAUDE.md](CLAUDE.md) for the layout.

## Quick start

Requirements: [uv](https://docs.astral.sh/uv/) and ffmpeg (macOS: `brew install uv ffmpeg`;
Windows: `winget install astral-sh.uv Gyan.FFmpeg`).

```bash
cp .env.example .env      # then fill in the Baserow token and table ids
uv sync                   # installs Python 3.12 and the dependencies
uv run mam doctor         # checks binaries, .env, local DB and Baserow (read-only)
uv run mam db migrate     # creates the local database (~/.mam/mam.sqlite)
```

Nothing changes files unless you choose **Run** (or pass `--no-dry-run`). Baserow is only
written when the **Baserow writes** switch in the top bar is on, or when you approve a review.

## Media manager

```bash
uv run mam serve          # http://127.0.0.1:8000
```

1. **Import**: browse to a folder (sample files, trimming samples, inbox or the NAS) and import it. Files are only indexed; originals are never changed.
2. **Files**: filter and select files, pick an action (Renamer, Verify, Measure loudness, Run full pipeline), then **Preview (dry run)** to see the proposed names, or **Run** to copy and rename into processed-files and open reviews. The list and the file page update by themselves while jobs run.
3. **Review**: files the Renamer could not settle on its own wait here (a preview never opens a review). Edit the date, WHAT and place with a live filename preview, then approve, reject the result or exclude the file.
4. **Jobs**: the queue, with errors and a retry button.
5. **Settings**: the current settings, and **Reset** to start from scratch (clears the local database and processed copies after making a backup; originals and Baserow are not touched). Also `uv run mam reset`.

## Useful commands

```bash
uv run mam rename <file-or-folder>               # Renamer proposals (dry run)
uv run mam organize <file> --what SB-1-19-31     # where the file organizer would put a file
uv run mam audio probe <file>                    # codec, bitrate, sample rate, duration
uv run mam audio measure <file>                  # loudness (LUFS) and true peak
uv run mam audio normalize <file> <out>          # whole-file loudness to -18 LUFS / -1 dBTP
uv run mam baserow schema media                  # live column schema and select options
uv run mam db export ./exports                   # backup of the local DB
uv run pytest                                    # fast tests
uv run pytest -m slow                            # tests on real samples in trimming-samples/
uv run ruff check . && uv run ruff format .      # lint and format
```
