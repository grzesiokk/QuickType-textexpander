# QuickType development status

All phases planned for the local Windows 11 edition are complete.

## Completed

- **Core expansion:** global Windows keyboard hook, Unicode insertion, immediate
  and delimiter triggers, word boundaries, and cursor restoration.
- **Templates:** date, time, clipboard, cursor marker, validation, and preview.
- **Desktop experience:** Polish and English UI, tray operation, pause, autostart,
  single instance, and portable one-file build.
- **Safety:** password-field detection, excluded applications, injected-event
  filtering, bounded in-memory matching buffer, and no telemetry.
- **Library management:** search, categories, favorites, usage statistics,
  application-specific snippets, duplication, JSON import, and JSON export.
- **Quick access:** configurable global picker for inserting enabled snippets.
- **Reliability:** transactional SQLite storage, schema migrations, atomic JSON
  backups, rotating automatic backups, an in-app recovery browser, and a safety
  copy created before every restore.
- **Fast library actions:** context-menu and double-click controls for enabling,
  disabling, and changing favorite status without opening the full editor.
- **Delivery:** unit and simulated-engine tests, PyInstaller build script,
  Windows smoke test procedure, GitHub Actions tests, and Windows build artifact.

## Intentionally outside this edition

Cloud synchronization, shared team libraries, images, arbitrary scripts,
interactive forms, and macOS support are not planned for this private offline
edition. Keeping these features out preserves the application's local-only
privacy and compact scope.
