# osu!coach

A personal trainer for osu! stable. osu!coach reads your replays, rebuilds every play object by object and tells
you what went wrong and why: your bad habits, your level in each skillset and how it changes over time, what to
train, maps that train it, and the tablet area or keyboard settings to change when the problem is your setup.

Windows only, osu! stable only (not lazer), osu!standard only.

## Install

1. Download **`osu-coach-Setup-<version>.exe`** from the [latest release](https://github.com/smakkia/osu-coach/releases/latest).
2. Run it. Windows may warn about an unknown publisher (the installer isn't signed): click **More info** > **Run
   anyway**. It installs osu!coach for your user (no administrator rights, nothing else to install) with a Start menu
   shortcut, and optionally a desktop one; if you play with a tablet, it can also install
   [OpenTabletDriver](https://opentabletdriver.net/) (optional, unticked).
3. The first time, a setup wizard asks for:
   - your **osu! folder** (usually found by itself);
   - your **osu! API** credentials, only needed to search maps on the osu! site: in your
     [osu! account settings](https://osu.ppy.sh/home/account/edit#oauth) click *New OAuth Application*, give it any
     name and the callback URL `http://localhost`, then copy the Client ID and Client Secret;
   - **tablet or mouse**: with OpenTabletDriver your area is read automatically, otherwise you type it in;
   - your **keyboard**: rapid trigger or mechanical, actuation point and rapid trigger distances.

   Then it builds your profile from your recent plays (a minute or two).

To update, run the new version's setup: your settings, profile and API credentials (in `%LOCALAPPDATA%\osu-coach`)
are kept, also when you uninstall it from the Windows settings.

**From the source code** (to change it): download the project (**Code** > **Download ZIP**) into a folder you'll keep
and double-click **`install.bat`**: it installs Python if needed (it asks first), the libraries and the map data, and
adds the shortcuts. To build the installer: `.venv\Scripts\python -m pip install -r requirements-dev.txt`, install
[Inno Setup 6](https://jrsoftware.org/isinfo.php), then run `tools\build-setup.ps1` (it writes
`dist\osu-coach-Setup-<version>.exe`, the version being `osu_coach/__init__.py`'s).

## Using it

Open **osu!coach** from the shortcut. The window has these sections:

- **Profile**: your bad habits, in order of how much they cost you, each with a fix; tablet area and rapid trigger
  advice; how your recent plays are going.
- **Skills**: your numbers per skillset: streams (minimum, comfortable and maximum BPM), alt, finger control,
  jumps, flow aim, sliders, stamina, high AR, reading, accuracy.
- **Improvement**: a rating per skillset, updated after every play, day by day over the last 90 days (about 1200
  for a balanced player; +400 means ten times the odds of passing the same pattern).
- **Replay analysis**: pick a replay (or import an `.osr`) and press **Analyze**: the play in a replay viewer, every
  mistake with its timestamp and reason (click it to watch that moment), and what to train. The viewer uses your
  osu! skin (images and hitsounds; another skin can be picked in Settings).
- **Beatmap search**: find maps by name, skillset, stars, AR, CS, OD, BPM and length, in your Songs folder or on the
  osu! site; tick **Recommended for me** for maps a step above your level in the skillsets you pick. Click a map to
  open its page, or download it (then press F5 in osu!'s song select).
- **Settings**: everything from the setup wizard, the search defaults and how sensitive the advice is; **Clear cache**
  frees the space taken by what searching the osu! site downloaded.

Tips:
- The more plays, the better: the profile and the skills need about 100 recent plays to be reliable.
- **Recompute** (Profile, Skills) and **Update** (Improvement) read your newest plays.
- The first start reads all your replays once, which takes a couple of minutes; after that only new ones.

## Your data

Your settings, API credentials and caches stay on your PC in `%LOCALAPPDATA%\osu-coach`. osu!coach only reads your
osu! folder; the only thing it writes there is the `.osz` files you choose to download into Songs.

Not supported: Relax/Autopilot replays, osu!lazer, modes other than standard.

## Command line

Everything is also available as commands, e.g. `osu-coach.bat analyze` (latest replay), `osu-coach.bat profile`,
`osu-coach.bat skills`, `osu-coach.bat recommend`, `osu-coach.bat search --type stream --stars 6-7`.
`osu-coach.bat --help` lists them all.

## License

MIT, see [LICENSE](LICENSE).
