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

Everything that changes files or Baserow is a dry run unless you pass `--no-dry-run`
(files) or set `BASEROW_APPLY=true` (Baserow).

## Useful commands

```bash
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
