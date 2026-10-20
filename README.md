# Photo Studio — native desktop packaging

This project is **source code for a native desktop build**, not a prebuilt app.

## What changes from 7.1

* `native_main.py` starts a local-only Python service and displays the existing Photo Studio web interface **inside a Qt WebEngine desktop window**, not in the user's browser.
* The window and the local service share one application lifetime. Close the window to shut down the service.
* No terminal window is opened by the installer or by the main application.
* Facy AI's separately authenticated Playwright browser remains external for login and processing; it is **not** the main app interface.
* The application includes Pillow, Playwright and a bundled Chromium in the packaged binaries. On first start, no global Python installation or `pip` is needed.

## Obtaining real installers

Because macOS and Windows produce different native binary formats, builds must run on each operating system. The included GitHub Actions workflow generates:

* `PhotoStudio-macOS.dmg` — contains `Photo Studio.app`, drag it to Applications.
* `PhotoStudio-Windows-Setup.exe` — graphical setup wizard, Start menu shortcut, optional desktop icon.

Publish this project into a repository and run **Actions → Photo Studio Desktop — macOS and Windows → Run workflow**. Download its two artifacts from the workflow page. The workflow is not yet run or validated on either target OS.

## Important limitations

* These source files were statically validated on Linux. No executable `.app`/`.exe` has been created or run in this session.
* Unsigned macOS apps may be blocked by Gatekeeper; production distribution requires Apple Developer ID signing and notarization. Similarly Windows binaries may trigger SmartScreen until signed and trusted.
* Qt WebEngine + bundled Chromium significantly increase distributable size.
* Facy AI depends on a third-party service and requires live account testing.
* If a workflow build fails, inspect its logs and repair packaging before calling the release ready.

Do not rename this ZIP a Mac app. It is only a native-packaging source project.

## What a user sees

1. macOS: download a `.dmg`, drag `Photo Studio.app` into Applications, double-click the app. Windows: double-click `PhotoStudio-Windows-Setup.exe`, click through the wizard, launch from Start menu.
2. Photo Studio opens its own desktop window. The normal app never navigates to a Chrome/Safari tab.
3. Processing and output folder selection run from inside Photo Studio. The Facy AI login flow opens a separate Chromium window because external-service authentication requires a browser session.

A single cross-platform binary is not feasible; the two platform installers can share exactly the same code and visual design.
