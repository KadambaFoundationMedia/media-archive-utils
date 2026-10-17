# Local DB helper

## Table of contents
- Goals
- Requirements

## Goals
This helper is responsible for setting up and maintaining a local (sql) database. This database is used by the media archive portal and all included tools during file processing. The database keeps track of the media files, their processing status and required metadata that becomes available during processing. Also any user activity is logged here. 

## Requirements
- local database application
- Easy export/import
- Tools must have read/write access
- Media portal must have read/write access
- Baserow updates are handled by tools/baserow/SPECS.md
