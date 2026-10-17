# Kirtan Splitter Tool

## Table of contents
- Goals
- Requirements
- Determining the mantra type
  - Filename convention per mantra type
- Example of a split file and renaming
- Examples of recordings with timestamps
  -- File examples

## Goals
A recording can be a combination of kirtan (singing) and the rest of the recording, which usually is the class or lecture. Standard recordings (during the morning and evening programs in ISKCON temples) always start with singing certain mantra's like "Jaya radha-madhava or Jaya Jaya sri Caitanya, Jaya Nityananda". The singing usually takes anywhere from 5-15 minutes. The goal is to determine if the recording is a combination of kirtan and class, and to split it into 2 parts. This tool must determine the exact timestamp where the kirtan ends and split it into 2 files; the kirtan and the class. Both files should be saved as separate files and renamed according to our filenaming convention.
If the singing continues throughout the whole recording it is a "kirtan recording" and should not be split!

## Requirements
- Determine the timestamp where the kirtan (the singing) start and ends.
  - Use common phrases to determine timestamps
- Split the recording into 2 files: kirtan and class
- Trim any silence before the kirtan (singing) starts
- Both files should be renamed again by the "Renamer Tool" and the media table should be updated accordingly!
- Kirtan files are always moved to their final location in the default processed file location (see .env)
- Video files must be processed first: the audio must be fully extracted and saved as a separate audio (mp3) in its original quality. The audio filename should be match the video filename.
- A dry-run option must be added.
- See @docs/references/editing-standard.md
- See @docs/references/recording-common-phrases.md

## Determining the mantra type
The type of mantra in a recording could be anything but there are standard mantra’s that are sung. Here is a list:
- Jaya radha-madhava: Is always sung before a Srimad-bhagavatam and Bhagavad-gita, followed by the maha-mantra
example: https://srimayapurdhama.com/vsong/jaya-radha-madhava/
- Sri Panca-tattva mantra before a CC class: (Jaya Jaya Sri Caitanya, Jaya Nityananda)
example https://kksongs.org/songs/j/jayajayasrikrsnacaitanya.html
- Nrsimha pranama - Namaste narasimha ajam, prahladala-dadayine, etc
example: https://srimayapurdhama.com/vsong/nrsimhadeva-arati/

### Filename convention per mantra types
- Maha-mantra → Kirtan
- Jaya radha-madhava → Jaya-radha-madhava
- Sri Panca-tattva → Jaya-Jaya-Sri-Caitanya
- Nrishmadeva → Nrsimha-pranama

## Examples of recordings with timestamps
- See folder trimming-samples/ for the review media files. Do not split these files!

### File: trimming-samples/2008-01-04-2.mp3
Kirtan ends: 28:13:048
Splitting timestamp: 28:13:048
Class starts: 29:19:929
Common marker: Not available
- Kirtan filename: 2008-01-04-2_KKS_<kirtan type>.mp3
- Class filename: 2008-01-04-2_KKS_class.mp3

### File: trimming-samples/2012-01-02_KKS_CC-Talk_Simhachalam_de.mp3
Kirtan ends: 7:39:592
Splitting timestamp: 7:39:592
Class starts: 7:59:918
Common marker: Not available
- Kirtan filename: 2008-01-04-2_KKS_<kirtan type>.mp3
- Class filename: 2008-01-04-2_KKS_class.mp3

### File: trimming-samples/HH Kadamba Kanana Swami - SB 3.6.6 - Sweden - 27_8_15.mp3
Kirtan ends: 2:29:396
Splitting timestamp: 2:29:396
Class starts: 2:30:189
Common marker: "om namo bhagavate vasudeva ajam"
- Kirtan filename: 2015-08-27_KKS_Jaya-radha-madhava_se.mp3
- Class filename: 2015-08-27_KKS_SB-3-6-6_se.mp3

### File: trimming-samples/KKS_S.B. 1.19.31(with Radha Madhava)_Oslo_29.8.11.WMA
Kirtan ends: 11:44:176
Splitting timestamp: 11:44:176
Class starts: 12:20:945
Common marker: Not availabe but speaker starts with "Its thirthy one, first canto"
- Kirtan filename: 2011-08-29_KKS_Jaya-radha-madhava_oslo-noi.mp3
- Class filename:  2011-08-29_KKS_SB-1-19-31_Oslo-no.mp3

### File: trimming-samples/Vyasa-puja 2015/ZOOM0004.MP3
Kirtan ends: 6:21:927
Splitting timestamp: 6:21:927
Class starts: 7:30:397
Common marker: "Reading from Srimad Bhagavatam"
- Kirtan filename: 2015-MM-DD_KKS_Jaya-radha-madhava.mp3
- Class filename: 2015-MM-DD_KKS_ZOOM0004.mp3
