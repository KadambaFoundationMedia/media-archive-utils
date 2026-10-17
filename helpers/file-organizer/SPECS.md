# File organizer helper

## Table of contents
- Goals
- Requirements
- Processed file location
- Duplicates filenames

## Goals
This helper is responsible for moving semi or fully processed files to their final location. The final location is determined by the "WHAT" in the filename (@docs/references/filenaming-convention.md). The "WHAT" should be determined from the original filename or is discovered during the processing of the file. When the "WHAT" information is not discovered or provided manually the file should be moved to a temporary location for future (re)processing.

## Requirements
- Will be called by tools to move files to their final location
- Use the helpers/local-db/SPECS.md for the required subfolder name

## Processed file location
Semi or fully processed files will be moved to their final location. The .env defines the root PATH for processed files and the "WHAT" determines the subfolder name. The "WHAT" is matched against the available terms (table "category_terms_folder") to determine the correct subdfolder name.

For example, the filename contains "SB". This is matched to the category "Srimad Bhagavatam" and the subfolder name is "Srimad-bhagavatam". See the Baserow table "category_terms_folder" and columns for matching purposes.

Originals on the NAS are never moved: the organizer copies files into the processed root. Files without a WHAT are copied to `<processed root>/_unresolved/` and moved to their category folder when a later tool or reviewer discovers the WHAT. The history is kept in the local DB.

## Name clashes
When a different file (different size/content) already exists with the same final name, append `-2`, `-3`, ... before the extension and flag the file for review.

# Duplicates filenames
When duplicate filenames are discovered and the file size also matches, the duplicate file should be saved also with a _duplicate in the filename. For example "2018-01-09_KKS_SB-9-20-20_Vrindavan-in_duplicate.mp3".