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

## Download

Download the newest beta package:

[**Download DBDCompanionOverlay.zip**](https://github.com/Werdtuy/DBD_MAP_Overlay/releases/download/latest-beta/DBDCompanionOverlay.zip)

Extract the zip and run `DBDCompanionOverlay.exe`.

Tesseract OCR must be installed separately. Windows installation options are listed in the [official Tesseract installation guide](https://github.com/tesseract-ocr/tessdoc/blob/main/Installation.md). If it is not found automatically, choose its executable path in the app settings.

## Hens Callout Maps

Use **Update Hens Maps** in the sidebar to download or refresh callout maps from [hens333.com/callouts](https://hens333.com/callouts) into `Maps/Hens Callouts/`. The app also checks this cache on startup. Existing cached images are skipped, so it only downloads maps that are missing locally.

Map callout credit: [Hens333 callouts website](https://hens333.com/callouts). The source page credits the images to Lethia and identifies the page as Zexov's modified version of the original build by Broosley and Evo from Hens' Discord.

Startup crashes are also written to `startup_error.log` beside the executable.

Global hotkeys may require running the packaged app as administrator depending on your Windows configuration.

## Updates

The updater is integrated into `DBDCompanionOverlay.exe`. The app does not check or install updates automatically. The top status bar shows the running beta version. Use **Check for Updates** there when you want to look for a newer package. If one exists, the app shows its changelog and lets you choose **Update** or **Not Now**. After accepting an update, reopen the app once it closes.

To test an update, open an older packaged version, select **Check for Updates**, review the changelog, and choose **Update**. Reopen the app after it closes and confirm that the newer beta version appears in the top status bar.

New beta downloads are published on the [GitHub Releases page](https://github.com/Werdtuy/DBD_MAP_Overlay/releases). Download `DBDCompanionOverlay.zip` when installing the app manually.
