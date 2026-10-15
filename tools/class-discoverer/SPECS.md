# Class Discoverer Tool

## Table of contents
- Tool Introduction
- Tool summary
- Requirements
- Standard verse structure
- Class (recording) structure
- Common phrases
  - Exceptions
- Generating a title

## Tool Introduction
This tool will first transcribe the class recording in full and save the transcription. Next it will discover the type of class in a recording. This is the "WHAT" part of our filename convention. When the filename already provides the "WHAT" information this is used by this tool to confirm it. When the filename does not provide enough evidence this tool will try to determine which type of scripture is discussed in the recording. When the "WHAT" is discovered, the filename is enriched with this new information. Last but not least the transcription will be analysed and a fitting title will be generated from the class.

All recordings are from ISKCON temples, (the International Society for Krishna consciousness, also known as the “Hare Krishna movement) and other venues. During a class the speaker discusses certain parts of the Vaisnava literature like the Srimad bhagavatam, Bhagavad-gita and Caitanya-caritamrta. All the scriptures are available on https://vedabase.io and this is the authoratative source on the teachings of A. C. Bhaktivedanta Swami Prabhupada. 

## Tool summary
- Transcribe the full recording
  - Save the transcription to a new file
- Determine the scripture and verse
  - Use the https://vedabase.io for evidence

## Requirements
- Transcribe the full recording using a local whisper model that is optimised for MacOS (MLX)
  - Always use Whisper Large Turbo, latest version, optmised for MacOS (MLX) or similar models. The goal is to transcribe the class as fast and accurate as possible, but we dont want to sacrife transcribing speed compared to 100% quality.  

## Standard verse structure
See @docs/references/verse-structure.md

## Class (recording) structure
See @docs/references/recording-structure.md

## Common phrases
See @docs/references/recording-common-phrases.md

