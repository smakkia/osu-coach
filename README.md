# osu!coach

osu!coach analyses your osu! stable replays and rebuilds, object by object, what happened: the timing error, where
the cursor was at each click, why an object was missed, where a slider broke. From that it gives advice: what went
wrong on this map and why, your bad habits (the mistakes that keep coming back in your recent plays), your level in
each skillset, maps to train them, and the settings to change (tablet area, sensitivity, keyboard) when the problem
is the setup.

Windows only, osu! stable only (not lazer), osu!standard only.

## Install

1. Download the project (green **Code** button > **Download ZIP**, then extract it; or `git clone` it) into a folder
   you'll keep, e.g. `Documents\osu-coach`.
2. Double-click **`install.bat`**. It:
   - finds Python 3.11+ or installs Python 3.12 with winget (it asks first);
   - creates a private environment (`.venv`) with the libraries (numpy, scikit-learn);
   - downloads the shared map data (the map type guesser, which makes the search on the osu! site much faster);
   - optionally installs [OpenTabletDriver](https://opentabletdriver.net/) with winget (it asks, default no);
   - adds an **osu!coach** shortcut to the desktop and the Start menu, and opens the app.
3. The first time, a **setup wizard** asks for:
   - your **osu! folder** (found by itself when it's in `%LOCALAPPDATA%\osu!`);
   - your **osu! API** credentials: in your [osu! account settings](https://osu.ppy.sh/home/account/edit#oauth),
     *New OAuth Application*, any name, callback URL `http://localhost`, then copy the Client ID and Client Secret.
     Only the search on the osu! site needs them;
   - **tablet or mouse**: with OpenTabletDriver the tablet area is read automatically; with another driver you type
     it in; with a mouse, your sensitivity and DPI;
   - your **keyboard**: rapid trigger or mechanical, actuation point, rapid trigger press/release distances.

   Then it builds your profile from your recent plays (a minute or two). Everything can be changed later in
   Settings, where the wizard can be run again.

Your settings, the API credentials and all caches live in `%LOCALAPPDATA%\osu-coach`, never in the project folder.
osu!coach only reads the osu! folder: it never changes it (downloaded `.osz` files are put in Songs for osu! to
import).

To update: download the new version over the old one (or `git pull`) and run `install.bat` again.

## The window

`osu-coach.bat` (or the shortcut) opens the window: a local page (`osu_coach/ui/`) served by `gui.py` and shown in an
Edge (or Chrome) window without the browser bars. It stops by itself when you close the window. Sections:

- **Profile**: bad habits grouped by area, in order of how much they cost you, each with a fix (for accuracy drops:
  train accuracy when the UR is high, change the offset when you're off time); area and rapid trigger advice (ghost
  taps: raise the actuation point or the press distance; missed taps: lower the actuation point or the release
  distance); your trend over the recent plays. The setup advice follows the cutoffs in Settings at once.
- **Skills** (`skillsets.py`): streams by length (minimum BPM with an acceptable UR, comfortable BPM, maximum BPM
  you still finish), alt UR every 10 BPM, finger control by number of notes, jumps by distance (mean distance from
  the circle centre, top BPM), flow aim, sliders by speed, plus stamina, high AR, reading and accuracy.
  "Recompute" reads your recent plays again for both pages.
- **Improvement** (`elo.py`): a rating per skillset, updated play after play as in chess, and its value at the end of
  every day of the last 90. Every play is a series of challenges (a stream to finish, a jump to hit, a slider to hold,
  a circle to hit for a 300...) whose difficulty is measured on the map alone and turned into a fixed rating: a
  player's rating moves by how many challenges they pass beyond what it expected. The scales are the same for
  everyone, set once (`tools/calibrate-elo.py`) so that the reference player, a fairly balanced one, is at about 1200
  in every skillset; +400 means ten times the odds of passing the same challenge.
- **Replay analysis**: the replays in `Data\r` (map, date, combo, accuracy, misses), plus any imported from the
  file manager. "Analyze replay" shows the play in a Rewind-style viewer (cursor and keys, judgements, hit error bar,
  timeline with misses and sections); every problem, and the list of all mistakes with their timestamps, jumps to its
  moment and loops it. Then: area and rapid trigger advice, and what to train, also from your trend.
- **Beatmap search**: by name (artist, title, difficulty, mapper), skillsets to include or exclude (each with a
  minimum or maximum share of the map), sliders for stars, AR, CS, OD, BPM and length, ranked and/or loved, maximum
  number of maps; on the osu! site (maps you don't have: their `.osu` is downloaded and read), in your Songs folder,
  or both. Click a map for its page; `.osz` files download one by one or all together into Songs (F5 in osu!'s song
  select imports them). **Recommended for me** replaces the filters with the recommender (see "Map
  recommendations"): for each included skillset (or the three that cost you most), never-played maps a step above
  your level, from easiest to hardest, each with why it was picked.
- **Settings**: osu! folder, API credentials, your setup (tablet area, keyboard), search defaults, download mirror,
  map tag threshold, and the advice cutoffs (area and rapid trigger).

## Command line

Everything the window does is also a command (`osu-coach.bat <command>` or `.venv\Scripts\python -m osu_coach <command>`):

```powershell
osu-coach.bat analyze                   # latest replay: mistakes explained + bad habits
osu-coach.bat analyze path\to\x.osr --objects   # object by object
osu-coach.bat profile --last 100        # bad habits and setup advice over the last 100 plays
osu-coach.bat skills --last 100         # comfortable level and limit per skillset
osu-coach.bat model --validate          # where your misses come from, per component
osu-coach.bat recommend                 # maps from Songs that train your weak spots
osu-coach.bat maptype crystalia meal --mods NM,DT   # map type (jump, stream, tech...)
osu-coach.bat guess sweets hearts       # guessed type of ranked maps you don't have
osu-coach.bat search --type stream,aim --stars 6-7  # maps in Songs by type and filters
osu-coach.bat search --type stream --stars 6-7 --online --limit 20  # on the osu! site only, until 20 are found
osu-coach.bat config                    # show the setup (tablet area read from OpenTabletDriver)
osu-coach.bat config --keyboard rt --actuation 1.2 --rt-press 0.2 --rt-release 0.2   # rapid trigger
osu-coach.bat config --device mouse --sens 0.9 --dpi 1600          # mouse players
osu-coach.bat validate --limit 500      # compare the simulation with the replays' own counts
osu-coach.bat --osu-dir "D:\Games\osu!" analyze   # a custom osu! folder
```

The osu! folder is found by itself (`%LOCALAPPDATA%\osu!`, or the `OSU_DIR` variable); the Songs folder follows
`BeatmapDirectory` in `osu!.<user>.cfg`.

The first run builds the replay index (`%LOCALAPPDATA%\osu-coach\replay_index.json`): it reads the header of every
replay and the difficulty of every map, which takes a couple of minutes with a few thousand replays. After that only
new replays are read. The analyses use the replays of the player with the most replays in the folder (replays of
other players you downloaded or watched are ignored); `--player` picks another name.

## How it works

| Module | What it does |
|---|---|
| `replay.py` | `.osr` parser (header + LZMA frames) |
| `beatmap.py`, `curves.py` | `.osu` parser: timing, slider curves, ticks/repeats/ends as in stable, stacking |
| `difficulty.py` | CS/AR/OD with mods, radius, hit windows |
| `judge.py` | simulates stable's judgement frame by frame: notelock, follow circle, key logic, spinners |
| `locate.py` | finds the game folder, the replays, and a map from its MD5 (via `osu!.db`, falling back to hashing the files) |
| `replay_index.py` | on-disk index of the replays (player, mods, map, date, effective AR): picks plays without opening them |
| `collect.py` | judges the chosen plays in parallel and turns them into samples |
| `analysis.py` | UR, mean error, miss causes, slider breaks |
| `features.py` | per object: distance, angle, rhythm, density; classifies the patterns |
| `advice.py` | bad habits: rules that turn the statistics of many plays into advice |
| `explain.py` | the mistakes of one play, episode by episode, with the reason |
| `coach.py` | priorities per category: the map's score and the bad habits' score, kept apart |
| `keys.py` | key presses and releases: chatter, keys that don't reset |
| `setup.py`, `setup_advice.py` | hardware setup (config / OpenTabletDriver) and advice on area, sensitivity, keyboard |
| `skills.py` | skill profile: comfortable level and limit for each skillset |
| `skillsets.py` | the Skills page's statistics (streams, alt, finger control, jumps, flow aim, sliders) |
| `mapdb.py` | reads `osu!.db`: mode, status, length, star ratings, ids for links, never-played maps |
| `recommend.py` | map recommender: profiles maps with your model and picks those that train a component |
| `maptypes.py` | map type (jump, stream, alt, finger control/burst, tech, hybrids) and aim control, from the intense sections |
| `typeguess.py`, `typepred.py` | guesses the type of maps you don't have from what the osu! API says about them |
| `model.py` | one model for every note: attributes each miss to the components of the skill schema |
| `api.py`, `online.py` | osu! API v2 client (rate limited, cached) and `.osu` downloads (mirrors in parallel) |
| `calibrate.py` | runs everything on a dataset of replays grouped by skill level and summarises the statistics |
| `gui.py`, `ui/` | the window: a local server with a JSON API over the analysis modules, and the page (HTML/CSS/JS, replay viewer) |

The judgement logic follows danser-go's stable ruleset, used as the reference.

### Miss causes
- `aim`: you clicked in time but off the circle
- `misread`: the click landed on another note visible at that moment
- `timing`: you clicked on the circle but too early (outside the 50 window)
- `notelock`: the click was blocked because the previous object hadn't been hit yet
- `no_click`: no usable click

### Sliders
A slider's result is the share of head + ticks + repeats + end caught (all = 300, at least half = 100, some = 50).
Missing a tick or repeat is a slider break (the combo breaks); missing only the end isn't.

## Advice

`analyze` joins two sources:
- **this play**: every mistake becomes an episode (a stream, a jump, a slider, a cluster of 100s) with a timestamp
  and a cause read from the replay: where the cursor was at the click, whether you clicked before arriving or the
  cursor was slow, how many taps you made for the notes, how the timing drifted before the first miss, whether you
  released the key or left the follow circle. In streams the first miss is explained; the ones right after are the
  same loss of sync.
- **your bad habits**: where your recent plays go clearly worse than your own average (`--habits 50` by default,
  `--habits 0` to turn them off).

Every category has two separate scores, from 0 to 100:
- **map**: how much of this play's mistakes come from that category;
- **habits**: how much of the impact of your bad habits comes from that category.

Bad habits only count when the map has that kind of content (at least 20 objects and 5% of the map; for stamina, at
least 120 notes in dense sections). Specific bad habits need the specific content: "streams fall apart above 200 BPM"
only shows up if the map has streams that fast, "jumps wider than 6 radii" only if it has jumps that wide. The play's
mistakes are always listed, even in categories the map barely has. Each category also shows your error rate on this
map against your usual one.

### Categories

Speeds always come from the real time between notes (BPM = 15000 / gap in ms), never from the BPM written in the map,
which the mapper may have doubled or halved.

Aim, apart from the pattern, is classified by the distance from the previous note alone: flow aim below 2 radii, alt
aim from 2 to 5 radii, jump aim beyond 5 radii.

| Category | What it holds |
|---|---|
| Streams | 1/4 (or 1/8) notes at a steady rhythm: doubles (2), triples (3), bursts (4-9), streams (10-33), deathstreams (34+). From 180 BPM at any spacing up to 5 radii; between 165 and 180 only up to 1.6 radii |
| Alt | runs of 4+ notes between 120 and 165 BPM, or between 165 and 180 spaced wider than 1.6 radii, up to 5 radii |
| Jumps | single notes more than 5 radii from the previous one, and runs spaced wider than 5 radii at any speed |
| Irregular rhythms | notes or runs snapped to 1/3, 1/6, 1/12 or odd divisors, against the map's timing points |
| Sliders | slider breaks (ticks/repeats) and slider ends let go early |
| Reading | clicks on another visible note instead of the right one; misses with 10+ notes on screen |
| Accuracy | 100s and 50s on circles by pattern, early/late, a constant offset |
| Stamina | misses in sections with 8+ notes/s sustained for 20 s, beyond those expected for their patterns |
| DT / high AR | plays at an effective AR above 10, against how you do at normal AR on the same patterns |
| Other aim | misses on notes outside the other categories |
| Setup | tablet area / mouse sensitivity, keys (chatter, keys that don't reset) |

The thresholds are constants at the top of `features.py`, `advice.py`, `coach.py` and `setup_advice.py`.

### Setup (area, sensitivity, keys)

- **Area / sensitivity** (after [osu-aim-analyzer](https://github.com/rgbeing/osu-aim-analyzer)): on jumps, how much
  the click error grows with the jump distance. When you land past the circle on average (overaim) the advice is a
  larger area / a lower sensitivity, when short (underaim) a smaller area / a higher sensitivity, with the exact
  millimetres when the area is known, from the cutoff set in Settings (3% of the distance by default). Rotation and a
  constant shift are measured too.
- **Keys**: a release followed by a new press of the same key within 10 ms isn't a finger but chatter (or a rapid
  trigger that's too sensitive): raise the actuation point or the press distance. A stream/alt note with no tap while
  the key is still down from the previous note is a key that doesn't reset: lower the actuation point or the release
  distance. The advice follows `config --keyboard rt|mechanical`.

The tablet area is read from OpenTabletDriver (`%LOCALAPPDATA%\OpenTabletDriver\settings.json`); `config --area
80x48.5` (or the wizard) overrides it.

### Bad habits

Bad habits are about mistakes: where you do worse than your own average. Your overall form over time (improving or
getting worse?) is a different thing, shown as the trend.

Every piece of advice compares a slice of your plays (streams at some BPM, jumps beyond some distance, dense
sections...) with your own average, and only shows up when there are enough samples, the difference is statistically
clear and it comes from several episodes (at least 3 streams or 3 different maps). So it works at any level: it
describes where *you* do worse than your own average, not against a fixed standard. Stamina, busy screens and DT are
measured against the misses expected for the patterns of those sections, so a harder section isn't mistaken for
fatigue or a reading problem.

One play gives little data: `profile` over the last 50-100 plays is much more reliable.

## Skill profile

`skills` estimates, for each skillset, how far you play comfortably and where your limit is:

| Skillset | Scale | Comfortable | Limit |
|---|---|---|---|
| Tapping speed (timing) | BPM of long streams, with the UR scaled to 180 BPM | UR +25% over your usual | +50% |
| Streams, alt | BPM, by length (8/16/32/64 notes; alt 4/8/16) | runs cleared 85% of the time | broken half the time |
| Jumps | 1/2 BPM, by distance (6/8 radii), at the angle you usually play; the angle's effect apart | at most 2% misses | 8% misses |
| Irregular rhythms | notes per second | at most 2% misses | 8% misses |
| Finger control | tapping load over the last ~0.5 s: every note counts 1, every change of technique or rhythm 1 more | misses 1.25 times your usual | double |
| Sliders, head | slider speed (radii per second), without and with repeats | the head costs 1.25 times a circle on the same pattern | double |
| Slider aim (following) | slider speed, with the next note far away in time or right after (a 6-radius jump within 120 ms) | slider end let go 5% of the time | 15% |
| Stamina | notes played since the start of the play | timing error +10% over your usual, in the same play | +25% |
| High AR (reaction time) | effective AR above 10 (10 excluded), against how you do at AR 9-10 (included) | misses 1.25 times your usual | double |
| Reading | notes on screen at an effective AR below 9 (EZ included), for simple and complex patterns, against AR 9-10 | misses 1.25 times your usual | double |
| Accuracy | OD | 90% 300s | - |

A threshold on a single axis doesn't work. You play maps within your reach, so your fast streams tend to be short and
tight, and the share of clean streams barely moves with BPM. So streams, alt and jumps get a logistic regression on
your results with several factors at once (BPM, length, spacing; for jumps speed and distance), and the level is read
on reference patterns. Stamina and AR are compared with the misses expected for the patterns, in bands, with a rising
trend imposed. Accuracy comes from the distribution of your timing errors: the OD at which 90% of your hits would be
300s.

Sliders are extended circles: the head is aimed like a circle, then the slider ball has to be followed to the end
(slider aim). In the data the weak point is the head: combo breaks inside the slider are rare, while the head is
missed more than a circle on the same pattern, the more the faster the slider. Slider ends let go early (accuracy,
not combo) are shown too, apart for the cases where the next note comes within 120 ms of the end. Following is
measured on slider ends let go early: a note right after the end, and far away, pulls the cursor off early (on
Transhumanist it happens on 9-13% of the sliders).

Stamina measures how your timing holds: how much the error grows, within the same play, as the notes pile up (the
load decays very slowly, with a 30-minute constant). Comparing within the same play rules out a bad day and maps with
harder timing; the fast finger strain is held constant, so technique spikes aren't mistaken for fatigue.

AR 9-10 (included) isn't a skill: it's the reference. Reading (below AR 9) doesn't depend on AR alone: what counts is
how many notes are on screen (AR and density together) and how hard they are to tell apart. For every note the model
uses the number of visible notes, the overlaps (notes close to the one to hit but far along the path, i.e. the
pattern coming back over itself; streams and stacks don't count), the irregularity of the rhythm, and the changes of
angle and spacing among the visible notes, these factors weighing more the more notes are on screen. HD is a control,
since it removes the approach circles. The level is the number of notes on screen you handle, for simple and complex
patterns, translated into AR at 5 and 7 notes/s. Plays below AR 9 and above 10 are rarer, so each gets its own window
(`--ar-plays 100`), however far back.

`>` and `<` mean the value lies beyond the hardest (or easiest) part of what you played: there the model can't say
more. About 100 plays are needed; with fewer, some skillsets give no level. The profile is saved in
`%LOCALAPPDATA%\osu-coach\skills.json` and is the base of the map recommendations.

## One model

`model` estimates a single miss probability for every note, with the factors grouped into the components of the skill
schema: flow, alt and jump aim, aim control, slider heads, tapping speed, finger control, irregular rhythms, reading,
high AR, stamina. Fitted together, every miss is attributed once: a spaced stream is split between tapping speed and
aim instead of counting for two skills.

Every factor is built so that "more" means "harder"; its reference is where it adds nothing (0 for what a note has or
hasn't, your typical level for the loads every note carries, like finger strain, notes on screen and the point of the
map). For every note, the miss probability above that reference is split among the components in proportion to how
much each raises it. A factor that goes the other way in your data (e.g. AR above 10, because your DT plays are on
easier maps) gets no misses and is listed apart. With `--validate` the model is compared, on plays it hasn't seen, with
the per-pattern miss rates the advice used.

Some factors don't grow in a straight line (jump speed and distance, run timing, timing × spacing in runs): for them
the model uses a broken line, with the bends at percentiles of your data.

**Miss chains.** 43% of misses come right after another miss, and two thirds of those are notelocks: the note after
a miss is lost because of the miss, not because of its own difficulty. So the model only learns from first misses
(the notes right after a miss leave the fit) and measures apart, for every pattern, how many misses a first miss
brings on average: more than 3 in spaced streams, just over 1 on jumps. The expected misses are the expected first
misses times the pattern's chain, and the chain goes to the same cause as the first miss.

Being a run and its length stay in the model as controls (per note, a run is missed less than a single note) but get
no misses: they describe the structure, not a difficulty.

## Map recommendations

`recommend` looks in your Songs folder for maps of the types you want to train, a step above your level.

1. **Skillset = map type**: jump, stream, alt, finger control/burst, tech, aim control (see "Map types"). With
   `--skill jump,stream,aim` (aliases: tap = stream, finger or burst = finger control/burst, slider = tech, aim = aim
   control); without `--skill`, the terminal shows a menu with how much each skillset weighs on your misses (Enter =
   the 3 that cost you most).
2. **Candidates**: from `osu!.db`, never-played osu!standard difficulties, ranked (or approved), at least 30 seconds
   long, for each mod combination (NM and DT by default, `--mods NM,DT,HR`). `--loved` adds loved maps (among them
   are exploit maps, but they usually have extreme star ratings that the star range keeps out); `--any-status` takes
   everything (graveyard, pending...). Filtered by:
   - stars: `--stars 6-7.5` (default: the 10th to 90th percentile of what you play, widened by 0.3 below and 0.8
     above);
   - effective AR (reading) `--ar`, CS (precision) `--cs`, effective OD (accuracy) `--od`: with DT or HR, as played;
   - drain length as played (DT shortens it): `--length 1:30-3:00`, in minutes:seconds or seconds;
   - main BPM as played (×1.5 with DT): `--bpm 180-220`, the BPM of the timing point covering most of the map, from
     `osu!.db`.
   Ranges are written `8-9.5`, `8-` (from 8) or `-9` (up to 9). When the skillsets come from the menu, the ranges are
   asked one by one (Enter keeps the default), and whether to include loved maps.
3. **Only maps with the skillset**: to train jumps only Jump maps or hybrids with jump ("Jump + alt") count, for aim
   control those with more aim control than typical for their star rating (see "Map types"). Those where the
   skillset takes the most intense notes come first (at most 400 per skillset). Type and content depend on the map
   alone and are cached.
4. **Difficulty for you**:
   - streams: the BPM of the long streams must fall where your **timing** starts to give way. Your UR on long streams
     is scaled to 180 BPM (UR × BPM / 180: UR 150 at 180 BPM is as good as UR 100 at 270), per BPM band; maps go from
     the BPM where it rises 25% above your usual (minus 5) to where it rises 50%. Streams broken more than 40% of the
     time rule the map out, and the whole map can't go over 2.5 times your miss rate;
   - the others: the miss rate the model predicts, from 1 to 2.2 times your usual.
5. **Ladder**: 15 maps per skillset (`--count`), one difficulty per beatmap set, from easiest to hardest. For each
   map: stars, effective BPM, AR, CS and OD, length, type (`[Jump, aim control x1.21]`, the ratio to the typical for
   the star rating) and how much of the map is that skillset.

### Maps from osu! (`--online`)

With `recommend --online` candidates also come from osu!, through API v2 with your OAuth client (the setup wizard, or
`config --api-client-id <id> --api-client-secret`, then `config --api-test`). Two sources per skillset:

- **search**: the site's filters on your star range and, for tapping, on the BPM of your timing window (divided by
  1.5 for maps to play with DT). Result pages are cheap, downloads aren't: maps are sorted by what the API already
  says and only the most promising are downloaded;
- **co-occurrence**: players who appear in the top 50 of at least two reference maps of the skill are specialists;
  the maps of their top plays (with their mods) are candidates, counted by how many of them have them.

Only the `.osu` of each candidate is downloaded (no audio) into `%LOCALAPPDATA%\osu-coach\osu_files`, from mirrors in
parallel (osu.direct, catboy.best; osu.ppy.sh as the last resort, one request a second), each file checked against
the map's MD5; then it goes through the same pipeline as the maps in Songs. API responses and files are cached.

Before downloading, every candidate goes through the **guessed type** (`typeguess.py`): a model trained on the maps
in Songs guesses the type from what the API already says (stars, BPM, circles and sliders, AR/CS/OD, mapper, artist,
other sets of the same song, popularity), without the `.osu`. Maps that almost certainly lack the skillset are
skipped, those that almost certainly have it are downloaded first, the rest after. It only counts where the model is
confident enough (exact type 0.7, main type 0.7, aim control 0.8): on sets held out of training, 84-87% of exact types
right, 91% of main types, 88% of aim control. `guess` shows the guessed type of a map. `search --online` also uses
the guess only to choose what to download, then reads every candidate's `.osu` and shows its real type.

Every map downloaded by `recommend --online` and `search --online` stays: its real type replaces the guess and joins
the guesser's labels (`training\lowconf.json`), which the next `retrain` learns from.

`retrain` retrains the model (needs scikit-learn): it relabels every ranked map in Songs, rebuilds the data and saves
the model in `%LOCALAPPDATA%\osu-coach\typeguess.pkl` and the guesses in `predicted_types.json`. Run it when the
classification (`maptypes.py`) changes or many new maps arrive; `--no-relabel` skips the labelling, `--evaluate`
measures the accuracy, `--download-unsure 100` downloads and labels the 100 maps with the least sure guess and
retrains with them, `--fetch-ranked` first reads the metadata of every ranked map from the API (~760 requests, one a
second, about a quarter of an hour; useful now and then for newly ranked maps).

## Map types

**Players' top plays.** `profiles` downloads the top plays of 2000 random players across rank bands (`--per-band`,
`--seed`) into `%LOCALAPPDATA%\osu-coach\profiles.json` (one request a second).

**Embedding.** From those top plays a vector is learnt for every map+mods (`mapvec.py`: skip-gram as in word2vec, 32
dimensions). Two maps are close when they are in the top plays of the same players.

**Labels.** Maps are read from the `.osu` with their mods and classified on their **intense sections** only: 4-second
windows whose density or mean aim speed reaches 70% of the map's highest. The slow or easy parts don't count. HR
doesn't change the kind of map (only the AR, which matters for reading): HR maps are read without HR, with the
original spacing and slider speed. Every note goes into one category:

- **tech**: fast sliders, from 25 radii per second (32.5 with DT, 1.3 times: DT speeds up every slider, even the
  ordinary ones of jump maps); each weighs speed / threshold. Kick sliders (fast but shorter than 3.4 radii, nearly
  inside the follow circle) in a row, less than 300 ms apart, weigh 0.75 times the previous one each. Everything is
  then multiplied by the ratio between the median speed of the fast sliders and that of the map's spaced aim (2+
  radii), up to 1.5: sliders slower than the jumps are played as part of them and the missing share goes to jump
  (1HOPE SNIPER, Walk This Way!); faster ones are tech (Transhumanist). A map is **tech** when these sliders (with
  their weights) are more than 37% of the intense notes;
- **finger control/burst**: short groups of 2 to 9 notes (doubles, triples, bursts) from 165 BPM, under 2 radii, plus
  off-grid notes (snap changes involving 1/3, 1/6, 1/8...);
- **stream** and **alt**: the other runs of 4+ notes at a steady rhythm (10+ notes, or slower than 165 BPM, or spaced
  2+ radii):
  - spaced 2 to 4 radii: alt, at any BPM;
  - under 2 radii: stream from 165 BPM, alt below 165;
  - beyond 4 radii: jump;
  - the BPM limits accept 1 BPM less (164.9 counts as 165);
- **slider aim**: spaced aim (2+ radii) to or from a slider that has to be followed, long (5+ radii of path) or curvy
  (3+ radii and a path 1.3 times the head-to-tail distance), or out of a fast slider;
- **alt** outside runs too: aim from 2 to 4 radii (alt aim), like the 1/2s between the sliders of Running in the 90's;
- **jump**: aim beyond 4 radii.

Below 4 stars (nomod) alt only counts for notes from 125 BPM (as 1/4, as played: DT can bring them there): slower
ones are too slow to alternate, so their aim from 2 to 4 radii and their spaced runs count as jump, their tight runs
as stream.

**Aim control** is a separate factor next to the category, a property of the map: it is read without mods. The cursor
path goes through the notes and, for sliders from 2.4 radii, through head, ticks and end (the end is the point within
1.2 radii, the slider ball's tolerance, closest to the next note). Points less than 0.3 radii from the previous one
(stacked notes) or less than 20 ms (hit together) are skipped. At every point of the intense sections, with s the
spacing in radii, t the time, v = s/t and θ the angle (0° goes back, 180° goes straight on), four terms add up:
- **accelerations**: the change of speed as a ratio, |v after / v before − 1| (speeding up counts more than slowing
  down), only at the same rhythm (±10%), times (t / 1/4 at 180 BPM)^0.5; changes in a row build up (A = 0.75 A +
  0.25 change) and the term is 9 · A^0.5;
- **sharp angles**: (s / 2)^−0.5 / (0.6 t / 1/4 at 180 BPM)² · |cos(θ/2)|, tight turns on close notes;
- **wide angles**: (8 s / 4)^0.75 · (t / 1/2 at 180 BPM)^0.25 · sin²(θ/2) · (v / 24 radii/s)^0.5, fast jumps that
  carry on;
- **angle changes**: |θ after − θ before| / 180°, weighted up to e^0.6 more towards a wide angle and e^0.6 less
  towards a sharp one, plus 0.3 · sin²(θ) of turning (the cursor turning even when each turn equals the previous one),
  both only between moves of 2+ radii (streams always curve, that's flow); then times (v / 24)^0.5 and (1/2 at 180
  BPM / time between taps)², with the time between taps in map time (DT doesn't change it). Moves from 0.5 radii
  count; changes in a row build up (S = 0.75 S + 0.25 change) and the term is 0.5 · S^1.5.
The build-ups restart at breaks and at each intense section; the score is the mean over the intense points.

A map **has aim control** when its score reaches at least 0.95 times the typical one for maps of its star rating
(nomod), 1.29 · stars^0.95, taken from 150 random ranked maps: so aim control counts against the difficulty, and about
60% of ranked maps have it.

Otherwise the main category is the one with at least 25% of the intense notes and at least 1.5 times the second.
Other maps are **hybrids** of their two largest categories. Jump + slider aim counts as Jump, tech + slider aim as
Tech. In the window, a map is tagged with every skillset that has at least a set share of its intense notes (20% by
default, in Settings).

**Speed** is a tag: stream or finger control/burst maps (hybrids too) above 240 BPM as played, the map's main BPM,
×1.5 with DT. **Precision** is a tag: CS above 6 as played, ×1.3 with HR. **Reading** is a tag too, for maps from 5
stars (nomod) with an effective AR up to 8.5 that have finger control/burst on at least 25% of the intense notes and
aim control: many notes on screen, in groups, with changing movements. Maps **below 3 stars** (nomod) get no type and
no tags: they are too easy for one to matter.

The classification is in `maptypes.py` (results cached in `map_types.json`). `maptype <words>` shows the type of the
maps in your Songs that match (artist, title, difficulty or mapper; `--mods NM,DT`).

## Calibration on other players

```powershell
.venv\Scripts\python -m osu_coach calibrate D:\dataset
```

The dataset has one folder of replays per skill tier, plus an optional `maps` folder with the `.osu` files that aren't
in the local Songs folder:

```
dataset/
  1-top1k/        *.osr (subfolders too)
  2-rank10k-50k/  *.osr
  maps/           *.osu
```

For every tier the report shows the simulation's precision, the share and rate of misses per category and pattern,
where the thresholds fall, how many players get each piece of advice, and the setup metrics (aim scaling and
rotation, chatter, stuck keys). The advice is computed per player, so several replays of the same player are needed.
Results go to `dataset/calibration/`: `report.json`, `players.json` and `missing_maps.txt` with the MD5s of the
missing maps.

## Precision

On ~900 local replays the simulation reproduces the 300/100/50/miss counts exactly in 85% of the plays and gets about
0.04% of the objects wrong. The remaining limit comes from the replays themselves: stable saves frames at ~60 Hz
while the game judges at every rendered frame, so the moments in between have to be estimated. ScoreV2 plays judge
sliders differently and aren't simulated exactly.

Not supported: Relax/Autopilot, osu!lazer replays, modes other than standard.

## Releasing

The map type guesser and its guesses are too big for the repository, so they ship as a release asset that
`install.bat` downloads:

1. Build the archive (from your own `%LOCALAPPDATA%\osu-coach`, after a `retrain`):
   `powershell -ExecutionPolicy Bypass -File tools\make-data-zip.ps1` creates `dist\osu-coach-data.zip`.
2. Create a GitHub release and attach `osu-coach-data.zip` to it.
3. `install.ps1` downloads it from `https://github.com/<owner>/osu-coach/releases/latest/download/osu-coach-data.zip`
   (the `$DataUrl` line).

## License

MIT, see [LICENSE](LICENSE).
