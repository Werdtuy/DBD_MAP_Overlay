# Changelog

## Beta 1.31

- Fixed update checks so only versions newer than the running app are shown.

## Beta 1.3

- Integrated updates into the main app and removed the separate updater executable.
- Removed automatic startup update checks.
- Added an update confirmation dialog with the new version changelog and `Update` or `Not Now` choices.

## Beta 1.21

- Added the running app version to a persistent top status bar.
- Added a `Check for Updates` button with an in-app availability result.

## Beta 1.2

- Added `DBDCompanionUpdater.exe` beside the overlay app.
- Added quiet startup checks for GitHub beta updates.
- Added background update downloads that install after the overlay closes and relaunch the updated app.
- Added a GitHub Actions workflow that publishes the newest shareable zip as the `latest-beta` release.

## Beta 1.15

- Removed outdated manual map-file setup instructions from the README.

## Beta 1.14

- Added a remaining-time countdown over the temporary OCR scan box.

## Beta 1.13

- Added the configured toggle-overlay hotkey to the live preview header.
- Replaced the map placement dots with larger coordinate-labeled selection boxes.

## Beta 1.12

- Removed the public beta versioning explanation.

## Beta 1.11

- Replaced the rolling Unreleased changelog with explicit beta versions.

## Beta 1.1

- Added a configurable 4x4 edge-only overlay placement picker.
- Enlarged the overlay position picker.
- Moved map controls into a collapsible Map Settings section.

## Beta 1.01

- Fixed click-through styling so the overlay remains visible after launch.

## Beta 1.0

- Added Hens callout map caching and startup loading.
- Switched map detection to manual OCR force-update only.
- Added force-update hotkey display in the overlay readout and Detection tab.
- Made overlay readout two lines: detected map and accuracy.
- Added click-through overlay window support for Windows.
- Added automatic settings save/import behavior.
- Added Tesseract auto-detection and visible search output.
- Added startup-hidden map sidebar with a compact Maps button.
- Added app icon assets and darker Dead by Daylight-inspired UI styling.
- Added `Build.bat` and `scripts/build.py` for exe builds.
- Added automatic release zip creation at `release/DBDCompanionOverlay.zip`.
