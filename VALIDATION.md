# Native Desktop Build validation (2026-10-20)

Completed in this Linux session:
- Source derived from Photo Studio 7.1.
- Added Qt WebEngine desktop launcher; no external browser opened by the main desktop entrypoint.
- Added frozen worker-mode dispatch for Facy AI subprocesses.
- Created platform-specific build workflow (Apple Silicon, Intel Mac, Windows x64).
- Created `.dmg` assembly and Inno Setup build recipes.
- Python static compilation: passed.
- Existing processing/API test suite: 12 passed, 1 skipped.
- GitHub Actions YAML parsed; referenced OS runner labels verified against GitHub runner-image documentation.
- ZIP archive integrity: passed.

Not completed:
- Building `.dmg`, `.app` or Windows `.exe`: cannot compile macOS/Windows native binaries in Linux session.
- Any real Qt window test, Facy login test, final installed app smoke tests.
- Apple signing / notarization or Windows code signing.

**This project is build-ready source, not a downloadable installed application.** Do not claim it is final before both native workflow jobs run and testing passes on actual devices.
