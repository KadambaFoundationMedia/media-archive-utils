# Default ID3 Tags

Title
Artist
Album
Genre
Album Artist
Composer
Year
Comment
Track

## How the Renamer fills them (decided 2026-11-24)
The Renamer writes these tags to the processed copy of mp3, m4a and wma files (not video), every time it renames the file. The listed fields are always overwritten; a field with no value is removed. Other tags such as cover art stay.

- **Title:** the verse as in titles, then the title: `SB 9.20.20-27 - The King Refused to Accept`; only the verse while there is no title; without a verse the WHAT as words.
- **Artist, Album Artist, Composer:** `Kadamba Kanana Swami`.
- **Year:** the year of the date (empty when unknown).
- **Genre:** the `idv3_genre` column of the category in `category_terms_folder` (the category name when that is empty).
- **Album:** SB: `SB Canto <canto>` (the first number of the verse, so SB 1.9.31 is `SB Canto 1`); BG: `BG chapter <chapter>`; CC: `CC <Lila>-lila`; every recording without a verse: `<category> - <year>` (`Janmastami - 2019`).
- **Comment:** `date, place, country` (`2013-11-14, Vrindavan, India`); unknown parts are left out.
- **Publisher and Copyright:** `Kadamba Foundation` (decided 2026-11-25).
- **Language:** the two-letter code of the file's language (`en`; several languages are all written, e.g. `en,nl`).
- **Track:** empty.

## Tags example

Title		: SB 9.20.20-27 - The King Refused to Accept
Artist		: Kadamba Kanana Swami
Album		: SB Canto 9
Genre		: Srimad Bhagavatam
Album Artist: Kadamba Kanana Swami
Composer	: Kadamba Kanana Swami
Year		: 2013
Comments	: 2013-11-14, Vrindavan, India

This example above shows you how to use the meta data fields. As you may have noticed i use a certain system for the Album titles and Genres. Below is a list of custom Album titles and genres:
MP3 Genres, custom genres and album titles
Genres are used to broadly categorize media files. Album titles can be used to “group” certain tracks together in an album. An album will be displayed (with cover art if available) in most music managers and online services (like SoundCloud)

## Genres
Genres are always broad in terms. Here are a few examples:

Bhagavad-gita - 2019
Srimad Bhagavatam - 2010
Caitanya caritamrta - 1999


## Genres & Album titles

### Genres
Album titles Examples
Playlist title examples
App. day
App Bhaktisiddhanta - 2019
Radhastami - 2019
Janmastami - 2019
<festival/event name> <year>
Radhastami 2019
Audio Album
<Album title>
Premanjana
Audio Albums
Bhagavad-gita
BG chapter xx - <year>
BG chapter 17 - 2019
Bhagavad-gita 2019
Caitanya-caritamrta
CC Adi-lila
CC Madhya-lila
CC Antya-lila"
Caitanya-caritamrta 2019
Conversations
Conversations Oxford
Conversations
Disapp. Day
Disappearance day Bhaktisiddhanta Sarasvati Thakur - 2019
<festival/event name> <year>
SP disapp day 2019
Festivals
Janmastami - 2019
<festival name/date>
Janmstami 2019
Home programs
Super retreat Sydney 2014
home program Gita 2002
Home programs
Initiations
Initiation Vyasapuja 2012
Initiation Capetown 2002
Initiations 2019
Kirtans
Kirtan mela germany 2014
Mayapur kirtan mela 2015
Bhajans & Kirtans 2019
Nectar of Devotion
NOD chapter 14
Nectar of Devotion 2019
Nectar of Instruction
NOI Verse 6
Nectar of Instruction 2019
Questions & Answers
QA RY Durban 2012
Question & Answers
Seminars
Preaching from the news 2008
<Seminar title> - Seminar
Srimad Bhagavatam
Srimad Bhagavatam 1.1.12 - Prague, Czech Republic - 2017
Srimad Bhagavatam 2019
University programs
BYS - Durban, South Africa - 2004
University programs
Various
Bhakti vriksa - Melbourne, Australia - 2012
Various
Vyasa-puja
Vyasa Puja - Radhadesh, Belgium - 2012
Vyasa-puja 2019
Interviews
Interview Radio - Canberra, Australia - 2019
Interviews 2019
Harinamas
Harinama - Sydney, Australia - 2019
Harinamas 2019
TV programs
TV program - Bratislava, Slovakia - 2019
TV program 2019


## Album Titles
Album titles offer a more precise way to “group” tracks. And album titles always compliment the genre. For example:

Janmastami - 2019
BG chapter 10 - 2019
