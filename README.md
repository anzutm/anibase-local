# AniBase

AniBase turns your local anime folders into a private streaming library: browse posters, continue episodes, track progress, generate thumbnails/subtitles, and keep everything running quietly from a Windows or Linux tray app.

It is built for personal collections first. Your media stays on your machine, runtime data lives in your operating-system user profile, and the web dashboard is served locally by the app.

## Preview

<div align="center">

<table style="border-collapse: collapse; width: 100%; margin-bottom: 20px;">
<tr>
<td width="50%" style="padding: 12px; box-sizing: border-box;">

### Home
<img src="assets/screenshots/Home.jpg" alt="Home page" width="100%" style="border-radius: 16px; box-shadow: 0 4px 20px rgba(0,0,0,0.3);">

</td>
<td width="50%" style="padding: 12px; box-sizing: border-box;">

### Anime Detail
<img src="assets/screenshots/Anime.png" alt="Anime detail page" width="100%" style="border-radius: 16px; box-shadow: 0 4px 20px rgba(0,0,0,0.3);">

</td>
</tr>
<tr>
<td colspan="2" style="padding: 12px; box-sizing: border-box; text-align: center;">

### Player
<img src="assets/screenshots/Player.png" alt="Player" width="120%" style="border-radius: 16px; box-shadow: 0 4px 20px rgba(0,0,0,0.3);">

</td>
</tr>
</table>

</div>

## Features

* 📁 Scan local anime and movie folders.
* 🗂️ Organize titles into a browsable dashboard.
* ⏱️ Preserve watch history, watch status, and resume progress.
* 📥 Auto-import files into selected library folders
* 🔎 Fetch AniList metadata for posters, banners, genres, characters, studios, relations, and recommendations.
* 🧠 Cache poster, banner, metadata, character, seiyuu, thumbnail, subtitle, and episode data locally.
* 🏢 Show studio pages and airing schedules.
* ▶️ Stream episodes through the built-in web player.
* 💬 Support subtitles, generated VTT subtitle cache, thumbnails, auto next episode, and resume playback.
* 🎞️ Optionally open media in an external Media Player.
* 🖥️ Modern PySide6 tray launcher.
* 🔒 Single-instance guard.
* 📴 Graceful shutdown for server, scanner, sync, watchdog, and auto-import workers.
* 🟣 Optional Discord Rich Presence.
* 🌐 LAN access controls and multiple theme presets.

## Requirements

* 🐍 Python 3.12 or newer
* 🌐 Internet connection for metadata.
* 🟣 Optional: Discord desktop app for Rich Presence.
* 🎬 Install **K-Lite Codec Pack Full** if videos appear blank or cannot be played in the browser.

## Installation & Setup

Choose one path:
* 🚀 **Release** if you only want to use AniBase.
* 🛠️ **Developer** if you want to run from source and use the project-local cache.
* 📦 **Build Release** if you want to create `AniBase.exe`.

### 🚀 Release

**Step 1: Download or Unpack**

[**Download AniBase for Windows**](https://github.com/anzutm/anibase-local/releases/tag/v2026.07.15)

Download the Windows ZIP from the release assets, extract it, then run `AniBase.exe`.

**Step 2: Run the Application**

```text
AniBase.exe
```

**Step 3: Open the Dashboard**

Open the dashboard from the tray, or visit:

```text
http://127.0.0.1:5000/ or localhost:5000
```

Release builds store settings, database, watch history/status, cache, logs, and temp files in:

```text
%LOCALAPPDATA%\AniBase\
```

### 🛠️ Developer

The setup script creates `.venv`, installs all Python dependencies, and makes
sure FFmpeg and FFprobe are available:

```powershell
git clone https://github.com/anzutm/anibase-local.git
cd anibase-local
powershell -ExecutionPolicy Bypass -File .\setup_dev.ps1
.\.venv\Scripts\Activate.ps1
```

Run AniBase from the project folder:

```powershell
$env:ANIBASE_USE_PROJECT_RUNTIME="1"
pythonw tray_ui.py
or
python main.py
```

With `ANIBASE_USE_PROJECT_RUNTIME` enabled, AniBase uses the data stored in
the repository instead of `%LOCALAPPDATA%\AniBase\`, including:

```text
cache\
logs\
temp\
```

Open the dashboard from the tray or visit:

```text
http://127.0.0.1:5000/ or localhost:5000
```

### 📦 Build Release

**Step 1: Prepare the Developer Environment**

Complete the **Developer** setup first. The build script uses the same virtual environment and the dependencies from `requirements.txt`.

**Step 2: Build the Release**

```powershell
python build.py --exe-only
```

**Step 3: Locate the Release Output**

The build target is `onedir` and `windowed`. The release folder is:

```text
releases\AniBase v<version>\
```

The build is native: run it on Windows for `win64.zip`, or on Linux for
`linux-x86_64.tar.gz`. PyInstaller does not cross-compile between operating
systems. The included GitHub Actions workflow builds both variants when a `v*`
tag is pushed, or when **Build release** is run manually.

On Linux, runtime data is stored under `$XDG_DATA_HOME/AniBase` (normally
`~/.local/share/AniBase`). After extracting the archive, run:

```bash
chmod +x AniBase
./AniBase
```

## First Setup

On a fresh runtime directory, AniBase opens the setup flow. Add your folders and preferences:

* 📁 Library folder paths.
* 🎬 Movies folder path.
* 📥 Auto-import paths, if used.
* 🎞️ Media Player executable path, if used.
* 🟣 Discord Rich Presence, if used.
* 🎨 Theme preset preference.
* 🌐 LAN access preference.

## Playback Troubleshooting

If the player opens but the video is blank or does not play, install the
**K-Lite Codec Pack Full**, restart Windows, then try playing the video again.
This is only required for media formats or codecs that are not already
supported by the user's Windows/browser configuration.

## Notes

* 🏠 AniBase is intended for personal local-library use only.
* 🔐 Do not expose the Flask development server directly to the public internet.
* 🧳 Keep runtime data, cache, logs, and local media out of Git and release bundles.
* 🎬 Windows releases bundle FFmpeg and FFprobe; the external Media Player remains optional.
* 💾 Normal release usage stores data in `%LOCALAPPDATA%\AniBase\`.
* 🛠️ Developer runtime mode stores data in this repository's `cache/` and `logs/` folders.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

Third-party notices are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
