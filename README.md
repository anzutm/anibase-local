<div align="center">

<img src="static/logo_transparent_cropped.png" alt="AniBase logo" width="112">

# AniBase

### Your collection. Your own cinema.

Turn local anime and movies into a private streaming library.<br>
Browse, press play, and pick up right where you left off.

<p>
  <a href="https://github.com/anzutm/anibase-local/releases"><img src="https://img.shields.io/badge/Download-Releases-38d878?style=for-the-badge" alt="Download releases"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-30363d?style=for-the-badge" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/Python-3.12-30363d?style=for-the-badge" alt="Python 3.12">
</p>

**[Preview](#preview) · [Features](#features) · [Get started](#get-started) · [Develop](#run-from-source) · [Build](#build-a-release)**

<sub>Local media · Windows & Linux · Fansub ASS playback</sub>

</div>

---

## Preview

![AniBase Home — your private screening room](assets/screenshots/Home.png)

<details>
<summary><strong>Inside the library: anime details & player</strong></summary>

| Explore the story | Enjoy the episode |
| :---: | :---: |
| ![Anime detail](assets/screenshots/Anime.png) | ![Player](assets/screenshots/Player.png) |

</details>

## Features

| Your library, organized | Your playback, remembered |
| :--- | :--- |
| **One collection.** Anime, movies, seasons, and automatic imports. | **Keep watching.** Resume episodes and track watch progress. |
| **More than posters.** Studios, voice actors, schedules, and manual metadata matching. | **Fansub styling.** ASS positioning, effects, and embedded MKV fonts. |
| **Make it yours.** Theme presets, optional Discord presence, and controlled LAN access. | **Watch your way.** Built-in web player or an external media player. |

<sub>Metadata: AniList first, Tenrai fallback. Subtitles: Inter when the requested font is unavailable; WebVTT when ASS rendering is unavailable.</sub>

## Get started

1. **Download & extract** a package from [Releases](https://github.com/anzutm/anibase-local/releases).
2. **Launch** `AniBase.exe` on Windows or `./AniBase` on Linux.
3. **Connect your folders**, choose a theme, and explore your collection.

Open the dashboard from the tray or at **http://127.0.0.1:5000/**. Movies, auto-import, external players, and LAN access are configured in Settings.

Executable releases need no separate Python installation. Windows portable packages include FFmpeg/FFprobe. Internet access is needed to fetch metadata; your media stays on your machine.

<details>
<summary><strong>Playback tips & where your data lives</strong></summary>

Browser playback depends on codec support. If a video cannot play, try the external-player option. Native fullscreen/PiP may use basic subtitles instead of full ASS styling.

Settings, cache, database, watch history, logs, and temporary files live in:

- **Windows:** `%LOCALAPPDATA%\AniBase\`
- **Linux:** `$XDG_DATA_HOME/AniBase`, or `~/.local/share/AniBase` by default.

Set `ANIBASE_USE_PROJECT_RUNTIME=1` before launching to use project-local development data. Keep runtime data and media out of Git and release packages. AniBase is for personal/local use; do not expose its server directly to the public internet.

</details>

## Run from source

<details>
<summary><strong>Developer setup · Python 3.12</strong></summary>

On Windows:

```powershell
git clone https://github.com/anzutm/anibase-local.git
cd anibase-local
powershell -ExecutionPolicy Bypass -File .\setup_dev.ps1
.\.venv\Scripts\Activate.ps1
python tray_ui.py
```

The setup script installs dependencies and checks FFmpeg/FFprobe. Recreate an existing virtual environment if it uses another Python version. Use `python main.py` to run only the web server.

On Linux, create and activate a Python 3.12 virtual environment, install `requirements.txt`, and put `ffmpeg`/`ffprobe` on `PATH` before running `python tray_ui.py`.

</details>

## Build a release

<details>
<summary><strong>Build, validate & recover a package</strong></summary>

Use the development environment above with PyInstaller **6.11+ (below 7)** and FFmpeg/FFprobe on `PATH`. Build on the target operating system.

```powershell
python build.py --check
python build.py v1.3.4 --exe-only
python release_check.py
```

Replace `v1.3.4` with your version. `--check` validates prerequisites without creating output. The release checker inspects the latest executable release; launch it and test playback before distributing.

Output: `releases/AniBase v<version>/` plus a platform-specific ZIP or TAR.GZ. Packages include the ASS renderer, fonts, and licenses, with asset integrity checked before publishing.

**Compilation succeeded, but packaging failed?** Reuse `dist/AniBase` without compiling again:

```powershell
python build.py v1.3.4 --package-existing --check
python build.py v1.3.4 --package-existing
```

| Option | Purpose |
| :--- | :--- |
| `--source-only` | Create a source folder; the destination needs Python and FFmpeg/FFprobe. |
| `--keep-temp` | Retain compilation output. `--package-existing` always preserves `dist`. |
| `python release_check.py --project-only` | Check source and unit tests without a built release. |

Choose one of `--exe-only`, `--source-only`, or `--package-existing`. Without a mode, failed executable builds fall back to source. Failed packaging retains staging output at the path reported in the terminal.

</details>

---

<div align="center">

**A little escape. A whole new world.**

AniBase is [MIT licensed](LICENSE). Bundled software retains its own licenses.<br>
[Third-Party Notices](THIRD_PARTY_NOTICES.md)

</div>
