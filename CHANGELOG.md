# Changelog

All notable changes are recorded here. The project has one version number (`mam/__init__.py`,
shown by `mam --version` and in the portal footer). It follows [Semantic Versioning](https://semver.org/):
a new tool or feature raises the minor number, a fix raises the patch number, and a change that
breaks stored data or commands raises the major number. Each entry names the component it touches.

## [Unreleased]

### Project
- Versioning and this changelog started.

## [1.0.0] - 2026-11-01

First approved release: the helpers, the media manager and the Renamer.

### Media manager
- Web portal (`uv run mam serve`) with file list, filters, file page, reviews, jobs and settings.
- Job queue with live updates, Baserow switch (live or dry run), reset with automatic backup.
- Place and country lists from Baserow, with `place_country` aliases.

### Renamer
- Finds date, WHAT and WHERE from filename, folders, tags, sibling files, the Baserow media table and
  the travel schedule; copies to processed-files and opens reviews when unsure.
- Hand-written aliases beat real places; `Summercamp` gives the Summercamp category.

### Helpers
- Baserow, file organizer, local database and audio optimizer.
