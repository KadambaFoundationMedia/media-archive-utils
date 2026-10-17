# Media Archive Tools

## Project overview
Local tools for renaming and processing media files (audio/video) stored on a NAS, coordinated by a webbased media manager where single files or batches are sent to one or more tools. Most of the processing runs locally; some processing requires cloud services. The archive consist of audio, music, video, text, design files and ebooks. The focus is on audio and video files and they make up for the bulk of the archive. There are approximately 15TB of files that are located on a local NAS. The files are recordings of Kadamba Kanana Swami, an ISKCON guru, who travelled the world to various ISKCON centers. There he was recorded during the morning and evening programs (called classes) and during festivals and events. The recordings can be kirtans (mostly singing), classes, a combination of both, and sometimes workshops, seminars or event/festival addresses. Most of the recordings were added “as-is” without proper naming or editing but there is some folder structure.

The goal is to build those tools and the media manager to discover and properly rename those files, categorise them and edit the recordings to our standards. These tools must be able to run locally in a MacOS or Windows environment and must be portable. The tools are coordinated by the media manager where the tooling can be executed to process files and folders in batches. Secondly any recordings that are flagged can be reviewed, edited and approved or rejected using the media manager.

## Key documents
- Architecture: @ARCHITECTURE.md
- Tool contract (jobs, results, errors): @CONTRACTS.md
- Tool specs live in each tool's folder (tools/<name>/SPECS.md); read the relevant one before working on a tool.
- Helper specs live in each helpers's folder (helpers/<name>/SPECS.md); read the relevant one before working on a helper.
- Reference files live in docs/references/: read the revelant one before working on a tool/helper

## Reference documents
- Filenaming convention: @docs/references/filenaming-convention.md
- Verse structure: @docs/references/verse-structure.md
- Editing standards: @docs/references/editing-standard.md
- Common phrases: @docs/references/recording-common-phrases.md

## Helpers
- Baserow Helper (helpers/baserow/SPECS.md): This connects to Baserow and provides CRUD actions.
- File organizer (helpers/file-organizer/SPECS.md): This moves media files to their final location
- Local DB helper (helpers/local-db/SPECS.md): This keeps track of the status of each file that is processed

## Tools
- Media Manager (tools/media-archive-manager/): Webbased media manager for processing and reviewing media files
- Renamer (tools/renamer/): Find and rename files using our filename convention
- Kirtan Splitter (tools/kirtan-splitter/): Review audio and split the file when a combination file is found.
- Class discoverer (tools/class-discoverer/): Transcribe and analyse the type of class in the recording and compare it to the vedabase.io
- Content discoverer (tools/content-discoverer/): Generate a summary and relevant themes from the recording for Youtube
- Class trimmer (tools/class-trimmer/): Class trimmer Tool(docs/):  This will trim the class to our editing standard

## Repository layout
- tools/media-manager/  webbased file reviewer
- tools/<name>/         one folder per tool
- helpers/<name>/       one folder per helper
- docs/                 shared documentation
- docs/references       shared documentation for specific tools
- transcriptions/       folder where transcriptions are stored
- sample-files/         sample media files to be processed
- processed-files/<sublfolder>/      processed media files organised in subfolders
- trimming-samples/     sample files for the tools/kirtan-splitter/ and tools/class-discoverer/ tools

## Tech stack
- Language/framework: [TBD]
- Database: [TBD, e.g. SQLite on the local machine, never on the NAS share]
- Audio/video processing: [TBD, e.g. ffmpeg]

## Commands
- Run media manager: [TBD]
- Run tests: [TBD]
- Lint/format: [TBD]

## Rules (always follow)
- Never overwrite or delete original media files; write new files or use staging.
- Every tool/helper that changes files must support a dry-run mode.
- Never run tools/hlpers against the real NAS during development; use test fixtures in /sample-files/].
- Every rename or file change is recorded in the local database and in the media table (baserow)
- If a spec is unclear or marked undecided, ask instead of guessing.

## Conventions
- [TBD: code style, naming, error handling, logging]

## Open decisions
- [List anything undecided so Claude doesn't invent an answer]