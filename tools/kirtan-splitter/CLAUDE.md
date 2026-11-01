# Kirtan Splitter

Read SPECS.md here and "Kirtan-splitter" in @SPEC.md (SPEC.md wins where they differ). Code is in `mam/tools/kirtan_splitter/`:

- `phrases.py`: mantra types and which one follows from the scripture
- `classify.py`: singing/speech per window from the loudness pattern (numpy, local)
- `detect.py`: pure boundary detection and scoring (no files, no database)
- `audio.py`: ffmpeg silences and audio extraction from video
- `tool.py`: the job, the cut, naming, reviews

This tool does not transcribe (the class-discoverer does). Tests: `tests/test_kirtan_splitter.py` (synthetic audio and window lists; real samples are `-m slow`, run on a machine that has `trimming-samples/`).
