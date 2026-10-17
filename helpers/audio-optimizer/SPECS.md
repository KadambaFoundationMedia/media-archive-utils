# Audio Optimizer Helper

## Table of contents
- Goals
- Requirements
- Improving audibility whole recording
- Improve audibility of audience questions
  - Audience timestamp example

## Goals
This helper is responsible for improving the audibility of the whole recording and the audience questions.

## Requirements
- Use transcriptions/<file> for determining timestamps
- Keep audio processing local, no online processing

## Improving audibility whole recording
Improve the audibility of the whole recording by adjusting the gain and using other filters like compressing or normalising the audio. The gain should be set to audio industry standards that match Youtube video standards. If other processing tools will help improving the audibility they can be used as well.

Approved method (see @SPEC.md section 4):
- Measure integrated loudness, true peak and loudness range with ffmpeg `ebur128`.
- Target `-18 LUFS +/-1 LU` with a `-1 dBTP` ceiling.
- If the peaks allow it, apply a pure linear gain.
- Otherwise apply the gain followed by a peak limiter running at 4x the sample rate, and re-encode once more if limiting cost loudness. Mark the result `dynamic` so it can be flagged for review.
- The kirtan-splitter runs first, so the whole-recording gain is measured on the class without the kirtan.

## Improve audibility of audience questions
Review the complete retained class for questions, answers and other audience speech. Audience questions are often recorded farther away from the microphone and can be substantially quieter than the main speaker. Determine the complete audience-question sections and apply section-specific gain when needed so the questions are clearly intelligible and reasonably match the normal speech level of the class.

Detection must be recording-specific and must combine multiple forms of evidence:

- complete local transcription and voice-activity evidence;
- semantic question-and-answer cues in the recording's actual language, such as the main speaker
  asking for questions;
- speaker-change or speaker-diarization evidence that distinguishes an audience member from the
  main speaker; and
- active-speech loudness, pauses and other acoustic evidence.

Low volume by itself must never trigger gain. Do not misclassify quiet speech by the main speaker,
translations, chanting, collective responses, applause, laughter, coughs, room noise or unrelated
background conversation as an audience question.

For each verified audience turn:

- preserve the complete utterance and its surrounding context, including follow-up questions;
- place gain boundaries in natural pauses and verify that no words are clipped;
- measure active-speech loudness rather than including silence or room tone;
- use the robust typical or median active-speech loudness of the main class speaker as the local
  reference, not the integrated loudness of the whole recording;
- apply only the minimum measured gain needed to bring the audience speech close to that reference,
  normally within 2 LU while preserving natural conversational dynamics;
- apply the interval gain before the final whole-class loudness pass, so the completed class still
  meets the approved `-18 LUFS +/-1 LU` speech target and `-1 dBTP` ceiling; and
- defer the interval for Human review when speaker identity or boundaries are uncertain, speakers
  overlap, the required gain would violate the true-peak ceiling, or the gain would make background
  noise materially distracting. A gain requirement above 12 dB is always review-required.

Do not silently add compression, limiting, equalisation or noise reduction to compensate for a
poor audience recording. Any additional clarity processing remains file-specific and requires
evidence and an audible A/B review. Do not remove silence or room sound and do not add editorial
fades. Gain changes must be click-free and must not create audible pumping or level jumps.

Preserve the source codec, container, nominal bitrate, sample rate and channel count. Record the
source hash, exact interval, speaker evidence, reference loudness, original audience loudness,
applied gain, and before/after peak measurements. Final QA must include strict full-file decoding,
integrated-loudness and true-peak verification, a before/after transcript comparison, and audible
checks of every adjusted boundary.

Some pointers
treatment of silence, room sound, setup speech and announcements;
- target loudness, peak and dynamic-range policy;
- noise reduction, equalisation, compression and channel handling;
- fade-in/fade-out lengths;
- codec, container, sample rate, bit depth and export quality;

### Audience timestamp example
File: 'sample-files/2012/Radhadesh Jan 2012/2012-01-22_KKS_CC-Madhya-20-154_Radhadesh_be.mp3'
Timestamps:
- 01:00:22:064: Speakers says "Ok, Questions"
- 01:00:29:473: First audience question (-28.6 LUFS)

while the surrounding main-speaker passages measure approximately `-22.0` to `-21.3 LUFS`. This example demonstrates the requirement but its timestamps or gain must not be reused for another recording.

