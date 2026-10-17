# Architecture

## Table of contents
- Whisper optimisation for Apple MacOS

## Whisper optimisation for Apple MacOS
Transcription runs behind one transcriber interface with two backends:
- macOS (Apple Silicon): `mlx-whisper` with the large-v3-turbo model (default).
- Windows: whisper.cpp `whisper-cli` with the GGML large-v3-turbo model.

Both backends produce the same normalized segment contract and record the engine, version, model hash, backend and fallback evidence. See @SPEC.md for the full stack.

### whisper.cpp runner
The whisper.cpp runner is Homebrew's `whisper-cli` on macOS (compatibility path) and the winget/release build on Windows. On Apple
Silicon it uses the compiled Metal backend by default, the `large-v3-turbo` GGML model, automatic language detection, zero-temperature decoding, no carried text context, hardware-aware helper threads (six on the project M1 Max Metal path, with CPU headroom), and flash attention. The runner's timestamped JSON is normalized into the existing segment contract, and the binding records `whisper.cpp`, its version, model hash, selected backend, and fallback evidence. 

Audio-Editing fails closed when the GGML model is absent. The default `auto` device tries Metal and falls back to local CPU if the accelerator fails; an explicitly requested Metal-only run still fails rather than changing its execution contract. It never falls back executable/model remains a compatibility path for older private jobs. 

