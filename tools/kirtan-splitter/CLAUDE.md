# Kirtan Splitter

Read SPECS.md here and "Kirtan-splitter" in @SPEC.md (SPEC.md wins where they differ). The splitter does not transcribe; common phrases are ignored for now. Code is in `mam/tools/kirtan_splitter/`:

- `features.py`: local music/speech score per second (numpy, streamed from ffmpeg)
- `detect.py`: pure boundary detection and scoring (no files, no database)
- `phrases.py`: mantra type from the class's WHAT
- `audio.py`: ffmpeg silences and audio extraction from video
- `tool.py`: the job, the cut, naming, reviews

Tests: `tests/test_kirtan_splitter.py` (synthetic audio and scripted profiles; the real samples are `-m slow`, run on a machine that has `trimming-samples/`).
