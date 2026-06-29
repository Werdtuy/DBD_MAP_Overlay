# Changelog

## Beta 2.3

- Reworked Escape Streak sync around D1-backed lobby codes with player tags such as `Nikko#3213`.
- Added a lobby-style app flow with create/join, copyable lobby code, player slots, leave lobby, and shared streak sync.
- Added Cloudflare D1 schema/migration files for streak profiles and lobbies on your Worker domain.
- Added encrypted packaged `streak_config.json` support so users do not enter the streak Worker URL.
- Updated beta packaging and in-app updates to carry the optional streak sync sidecar when available.

## Beta 2.2

- Added secured admin endpoints to the Escape Streak Worker for listing, inspecting, resetting, updating, and deleting lobbies.
- Added a private local Streak Manager tool for managing all active shared streak lobbies with a Worker URL and admin token.
- Documented Cloudflare `STREAK_ADMIN_TOKEN` setup for the streak sync Worker.

## Beta 2.1

- Added online Escape Streak lobbies so players can create or join a shared streak by code.
- Added saved streak sync settings for server URL, display name, lobby code, and per-device player identity.
- Added a Cloudflare Worker + KV backend template for shared lobby state without committing private account secrets.
- Reworked the companion navigation toward the concept art with a custom icon rail and darker staged panels.

## Beta 2.0

- Replaced the companion settings window with a PySide6/Qt interface for a more customizable DBD-style layout.
- Added a toggleable Escape Streak module with lobby code, streak controls, and four team player slots.
- Added an optional Escape Streak HUD strip under the overlay map and inside the live preview.
- Restyled the app with darker panels, red accents, warmer text, larger placement controls, and icon-style tab labels.
- Removed the CustomTkinter runtime dependency from the launch path and build requirements.

## Beta 1.94

- Added Hens cache migration so existing `Rancid Abbatoir` files are renamed to `Rancid Abattoir` when maps update.

## Beta 1.93

- Treated OCR pipe characters as `I` before map matching to improve matches such as `SPRINGWOOD - BADHAM PRESCHOOL |`.
- Corrected the Hens import name `Rancid Abbatioar` to `Rancid Abattoir` while keeping the misspelling as an alias.

## Beta 1.92

- Moved Dead by Daylight session cleanup into the background watcher so it actively closes the overlay app when the game exits.
- Kept the watcher process alive after closing the overlay app so it can reopen the overlay next session.

## Beta 1.91

- Reduced Dead by Daylight exit detection to one 5-second check before closing the overlay app.
- Treated minimized watcher-style launches as auto-close sessions for compatibility with older background watchers.
- Made Dead by Daylight process detection more tolerant of process-name variations.

## Beta 1.9

- Made watcher-launched overlay sessions close automatically after Dead by Daylight exits.
- Kept the background watcher running so it can open the overlay again on the next Dead by Daylight launch.

## Beta 1.81

- Fixed packaged startup crashes by forcing watcher-spawned app processes to create a fresh PyInstaller runtime folder.

## Beta 1.8

- Added a low-overhead Windows startup watcher that opens the overlay automatically when Dead by Daylight launches.
- Made packaged app launches open with the settings window minimized by default.
- Updated future installs to stop the background watcher before replacing the executable.

## Beta 1.72

- Replaced public build instructions with a direct beta-package download link.
- Added clear beta download instructions to the GitHub prerelease page.

## Beta 1.71

- Restricted in-app updates to the executable and required runtime sidecar files only.
- Reduced shared release packages to the bare minimum needed to launch and update the app.

## Beta 1.7

- Replaced the plaintext activation sidecar with a tracked encrypted `license_config.json`.
- Made downloaded source packages self-contained so `Build.bat` works without private setup files.
- Shipped and embedded only the encrypted activation configuration in release builds.

## Beta 1.61

- Added a first-run private activation-URL prompt to `Build.bat` when the ignored local license configuration is missing.

## Beta 1.6

- Added a Settings tab showing the activated license key, access type, expiration date, remaining time, and device usage.
- Refreshed saved license details during the required validation check on every app launch.

## Beta 1.54

- Updated built-in GitHub links for the renamed `DBD_MAP_Overlay` repository.

## Beta 1.53

- Updated in-app installs to merge every shipped file and newly added folder from the release package.
- Preserved local settings, downloaded maps, and other user files that are not replaced by the release.

## Beta 1.52

- Fixed license activation after in-place updates from older beta builds.
- Embedded a packaged activation fallback and copied required sidecar files during future updates.

## Beta 1.51

- Updated the GitHub beta publisher to Node 24-compatible official actions.

## Beta 1.5

- Added required license-key activation before the overlay starts.
- Stored activated keys locally with Windows DPAPI protection.
- Kept license validation startup-only so gameplay has no additional background activity.

## Beta 1.39

- Removed private deployment details from the public project files.

## Beta 1.38

- Changed public update checks to use direct GitHub release downloads instead of the rate-limited anonymous GitHub API.
- Added a clear in-app status message when a private GitHub release cannot be accessed without a token.

## Beta 1.37

- Added a README checklist for testing the integrated update flow from an older packaged beta.

## Beta 1.36

- Fixed local builds with Microsoft Store Python installations that do not keep Visual C++ runtime DLLs beside `python.exe`.
- Kept the final packaged-runtime verification so incomplete executables are still blocked.

## Beta 1.35

- Fixed integrated updates by finishing installation after the app closes and asking the user to reopen it manually.
- Added a retry loop while replacing the executable so the installer waits until Windows releases the old packaged app.

## Beta 1.34

- Fixed the post-update relaunch so the new app does not inherit a removed PyInstaller temporary runtime folder.
- Added a short relaunch delay and a build check that blocks incomplete runtime packages from being published.

## Beta 1.33

- Added official Tesseract OCR links to the README setup section.

## Beta 1.32

- Added visible Hens333 callout-map credits and a link to the original callouts website.

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
