# Class Discoverer Tool

## Table of contents
- Introduction
- Goals
- Requirements
- Evidence and confidence
- Example file

## Introduction
This tool will try to determine, or confirm, the type of class or the scripture/verse that is being discussed in the recording. The filename, the transcription and other metadata should provide evidence. And the evidence is compared, and possible enriched, by comparing it to the vedabase (https://vedabase.io) which is the authoritative source of vaisnava scriptures. The type of class/scripture/verse determines the "WHAT" part of the filename convention. When the filename already provides the "WHAT" information this is used by this tool to confirm it. When the filename does not provide enough evidence this tool will try to determine which type of scripture is discussed in the recording. When the "WHAT" is discovered, the filename is enriched with this new information. Last but not least the transcription will be analysed and a fitting title will be generated from the class.

All recordings are from ISKCON temples, (the International Society for Krishna consciousness, also known as the “Hare Krishna movement) and other venues. During a class the speaker discusses certain parts of the Vaisnava literature like the Srimad bhagavatam, Bhagavad-gita and Caitanya-caritamrta. All the scriptures are available on https://vedabase.io and this is the authoratative source on the teachings of A. C. Bhaktivedanta Swami Prabhupada. 

## Goals
- Use @helpers/audio-optimizer/SPECS.md to improve the audio quality of the whole recording
- Transcribe the full recording
- Save the transcription to a new file in the default transcription folder
  - Use the tools/renamer/specs.md tool to rename it correctly
- Determine the scripture and verse from the filename, transcription or other metadata
  - Use the https://vedabase.io to confirm or enrich the "WHAT"
- Use tools/renamer/specs.md tool to rename the recording when new metadata is found

## Requirements
- Transcribe the full recording using a local whisper model that is optimised for MacOS (MLX)
  - Always use Whisper Large Turbo, latest version, optmised for MacOS (MLX) or similar models. The goal is to transcribe the class as fast and accurate as possible, but we dont want to sacrife transcribing speed compared to 100% quality.  
  - On Windows use whisper.cpp `whisper-cli` with the GGML large-v3-turbo model (see @ARCHITECTURE.md)
  - Cache the transcript by content hash so later tools (content-discoverer, class-trimmer) reuse it. The kirtan-splitter does not transcribe.
- The audio-optimizer is only used here for the whole-recording gain. Audience-question gain is done by the class-trimmer.
- When the transcript shows a different verse than the filename, keep the filename and flag the file for review (see @SPEC.md)
- Use common phrases to determine timestamps @docs/references/recording-common-phrases.md
- See @docs/references/verse-structure.md for the default verse structure
- See @docs/references/editing-standard.md for insights into the recording and editing standards

## Evidence and confidence
- The filename always takes precedence over other evidence
- The transcription can help confirm or enrich evidence and increase confidence

## Example file
File: trimming-samples/2008-01-04-2.mp3
Class start timestamp: 29:19:989
Common phrase: "Bhagavad-gita as it is, chapter"
Notes: The filename "2008-01-04-2.mp3" does not provide evidence about the class or scripture/verse. From the transcription it can be determined that this is a class on "Bhagavad-gita, 2nd chapter, text 51".
Process: The filename should be renamed to "2008-01-04_KKS_BG-2-51.mp3" by the renamer tool. The Renamer tool will also update the media table.
Warning: The gain level of the speaking part is very low and this will impact the transcription quality. So it is always advised to call the helpers/audio-optimizer/ to improve the audibility before transcribing!
