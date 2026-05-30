# DBD Companion Overlay

A lightweight Python companion overlay for Dead by Daylight. It shows local map images as a transparent, always-on-top overlay, switches maps from OCR-detected map names, and exposes a polished `customtkinter` settings app with live preview, logging, hotkeys, and map management.

## Features

- Transparent always-on-top overlay with configurable corner, monitor, opacity, size, zoom, border, and corner radius
- Local map library from `Maps/` with PNG, WEBP, and animated GIF/WEBP support
- Cached Hens callout maps from `https://hens333.com/callouts`, refreshed on app startup without re-downloading existing files
- OCR detection via `mss` + `pytesseract`, gated so scanning only runs while Dead by Daylight is focused
- Optional lightweight fallback template matching
- Smooth animated transitions and configurable animation speed
- Global hotkeys for toggle, reload, cycle variants, and manual map selection, active only while the game is focused
- Profiles/presets stored in JSON
- Built-in log console and live overlay preview
- Modular package layout suitable for PyInstaller packaging

## Setup

Install Python 3.11+ and [Tesseract OCR](https://github.com/tesseract-ocr/tesseract), then:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts\run.py
```

Tesseract must be installed separately. Windows installation options are listed in the [official Tesseract installation guide](https://github.com/tesseract-ocr/tessdoc/blob/main/Installation.md). If it is not on `PATH`, set its executable path in the app settings or edit `config/settings.json`.

## Project Layout

- `Build.bat` builds the overlay executable
- `assets/` contains the app icon and bundled visual assets
- `dbd_overlay/` contains the application code
- `scripts/` contains the development launcher and build helper scripts
- `Maps/`, `config/`, `logs/`, and `plugins/` are runtime folders used beside the app
- `build/` and `DBDCompanionOverlay.exe` are generated build output

## Hens Callout Maps

Use **Update Hens Maps** in the sidebar to download or refresh callout maps from [hens333.com/callouts](https://hens333.com/callouts) into `Maps/Hens Callouts/`. The app also checks this cache on startup. Existing cached images are skipped, so it only downloads maps that are missing locally.

Map callout credit: [Hens333 callouts website](https://hens333.com/callouts). The source page credits the images to Lethia and identifies the page as Zexov's modified version of the original build by Broosley and Evo from Hens' Discord.

## Packaging

To build a fresh Windows `.exe` for usage:

```bat
Build.bat
```

The script installs PyInstaller if needed and builds `DBDCompanionOverlay.exe` directly in this project folder. It also creates `release/DBDCompanionOverlay.zip`, which is the file to share. The zip includes the app plus empty `Maps/` and `config/` folders; the app will populate missing Hens maps from the web on first startup.

By default, the app is built without the black console window. If you need to debug a launch problem, build with a visible console:

```bat
Build.bat -Console
```

Startup crashes are also written to `startup_error.log` beside the executable.

Global hotkeys may require running the packaged app as administrator depending on your Windows configuration.

## Updates

The updater is integrated into `DBDCompanionOverlay.exe`. The app does not check or install updates automatically. The top status bar shows the running beta version. Use **Check for Updates** there when you want to look for a newer package. If one exists, the app shows its changelog and lets you choose **Update** or **Not Now**. After accepting an update, reopen the app once it closes.

The GitHub Actions workflow publishes a new `latest-beta` package after updates are pushed to `main`. Anonymous updates require the GitHub repository to be public. For a private repository, create `updater_config.json` beside the executables and set `github_token`, or set the `DBD_OVERLAY_GITHUB_TOKEN` environment variable. Do not distribute a personal token inside a shared zip.
