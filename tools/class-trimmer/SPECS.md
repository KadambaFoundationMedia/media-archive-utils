# Class Trimmer Tool

## Table of contents
- Introduction
- Goals
- Requirements
- File trimming example
  - Unedited Trimming timestamps
  - Edited sections
- Increase audibility of audience questions

## Introduction
You are working with live recordings of classes, seminars, workshops and other events. These recordings are from the morning/evening programs in ISKCON temples or at festivals and venues. The recording topics are based on Vaisnava literature like the Bhagavad-gita, Srimad-bhagavatam and others by A.C. Bhaktivedante Swami Prabhupada (BBT) and other acaryas (guru’s). Most recordings contain sanskrit verses. The Vedabase https://vedabase.io/en/ provides access to all the books that are discussed in these recordings. The Class Trimmer will determine what sections are available in the recording and edit them to our editing standards.

## Goals
The class trimmer will determine the sections of each recording and their timestamps. Next it will trim out the unwanted sections. After trimming it will also increase the audibility of the audience questions. The file trimming example below provides an example of the timestamps that need to be discovered. Afterwards the unwanted sections should be trimmed. 

## Requirements
- Use @docs/references/editing-standards.md
- Use @docs/references/recording-common-phrases.md
- Use @docs/references/verse-structure.md
- Use the transcriptions/<file> for this recording

## File trimming example
file: sample-files/2011-08-20_KKS_SB-1-19-31_fi.mp3
Warning: The timestamps below describe **only this example**; Other recordings will have different timestamps.

### Unedited Trimming timestamps

11:44:122 - Kirtan (singing) ends (Kirtan will be split by the kirtan-splitter tool eventually)
11:44:122 - 11:53:666 background ambience - TRIM
11:53:666 - 12:16:034 "Om namo bhagavate vasudeva ajam" - TRIM
12:16:034 - 12:23:478 Book and verse introdction - "First canto, chapter nine ..." (So this is a srimad Bhagavatam class) - KEEP
12:29 - 12:54 Background - TRIM
12:54 - 13:32 1x sanskrit verse by speaker - KEEP
13:32 - 17:32 Repeated sanskrit verse - TRIM
17:32 - 18:46 Synonyms - KEEP
18:46 - 19:05 English verse translation - KEEP
19:05 - 20:25 Purport - KEEP
20:25 - 21:15 Oblations - "Om ajnana timeranda shaj" - TRIM
21:15 - 01:43:33 Explanation and discussion and audience questions (the actual class) - KEEP

### Edited sections
When the file example is edited these sections should remain

- Book and verse introdction - "First canto, chapter nine ..."
- 1x sanskrit verse by speaker
- Synonyms
- English verse translation
- Purport
- Explanation and discussion of the purport (the actual class)

## Increase audibility of audience questions
Usually after the verse discussion is over the speaker will inquire from the audience if there are questions about his class. The gain level of those questions is usually much lower compared to the speaker. The goals is to increase the gain level of the questions to the same level as the speaker to make it more audible.
