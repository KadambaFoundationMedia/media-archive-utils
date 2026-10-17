# Media Manager Tool

## Table of contents
- Goals
- Requirements
- Design
- Layout
- Action menu

## Goals
The Media manager is a webbased portal where media files and folder can be added, reviewed and processed. Processing files and folders should be possible per file/folder and in batches. Files, and folders can be selected one-by-one or all of them. An action menu provides options to process the selected items. The file/folder list shows the file details and its status clearly. During file processing files can be flagged for review. Files that need to be reviewed have a detail page where metadata can be edited and approved through buttons (edit/approve/reject). When files are manually approved this should be recorded and updated.

## Requirements
- Must run locally on mac/windows
- does not require authentication 

## Design
- Use Bootstrap style layout and design
- Use Fontaweome free fonts
- dark/light mode switcher

## Layout
- Mobile friendly layout
- File/folder overview
Each row in the list should show the original file name and path, the renamed filename, the process status, important metadata like the recording date, title, location. 
- Search, filter and sorting options must be avaiable for the file/folder listing
- item detail page
Items that need reviewing have a separate detail page where data can be updated and saved

## Audio reviewing
The class trimmer tool determines the timestamps to trim the recording. But if the trimmer tool flags a file for review the media manager must provide a visual waveform with the trimming timestamps. The user must be able to review (listen to) the audio. The user can move each trimming section and approve each one manually. After approving the timestamps the class trimmer will trim the sections and approve the file.

## Action menu
The action menu lists all the available tools for file processing. See @CONTRACTS.md (Media File Processing flow) for all the tools, plus a "Run full pipeline" action. See @SPEC.md section 8 for the full UI spec.

## File statuses
Each media file can have one of these statuses:

- <Tool name> Review
- Not processed
- Processing <tool name>
- Processing complete
- Corrupted

If more statuses are required just propose them to the user. 