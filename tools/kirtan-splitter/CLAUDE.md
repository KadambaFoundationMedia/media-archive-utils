# Kirtan Splitter

Read SPECS.md here and "Kirtan-splitter" in @SPEC.md (SPEC.md wins where they differ). Code is in `mam/tools/kirtan_splitter/`:

- `phrases.py`: class-start phrases and mantra types
- `detect.py`: pure boundary detection and scoring (no files, no database)
- `audio.py`: ffmpeg silences and audio extraction from video
- `tool.py`: the job, the cut, naming, reviews

The transcriber is a helper (`mam/helpers/transcriber`). Tests: `tests/test_kirtan_splitter.py` (synthetic audio and a fake transcriber; real samples are `-m slow`, run on a machine that has `trimming-samples/`).
