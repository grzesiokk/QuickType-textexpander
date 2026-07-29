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
- **Backup catalog:** one filtered recovery browser for automatic, manual,
  before-import, before-restore, and other valid local QuickType backups.
- **Safe restore preview:** read-only added, changed, removed, and unchanged
  counts in the backup browser and final restore confirmation.
- **Backup inspection and management:** per-snippet restore differences,
  changed-field details, action filters, clipboard reports, catalog refresh,
  direct folder access, and confirmed deletion constrained to the backup
  directory.
- **Safe import:** preflight snippet and conflict counts, an exact conflict
  list, explicit merge, update, or replace modes, and a safety copy before
  every import.
- **Conflict-aware updates:** side-by-side expansion comparison and a third
  import mode that updates conflicts without deleting unrelated local snippets.
- **Fast library actions:** context-menu and double-click controls for enabling,
  disabling, and changing favorite status without opening the full editor.
- **Category management:** category counts, transactional bulk rename, and safe
  removal of category assignments without deleting snippets.
- **Usage insights:** total expansion count, ranked snippet usage, last-used
  timestamps, and selective or complete statistics reset.
- **Library workflows:** sortable columns with numeric usage ordering, live
  filtered-result counts, and JSON export of the currently visible subset.
- **Data maintenance:** on-demand manual backups, SQLite integrity checks, data
  size and backup counts, and direct access to the portable data folder.
- **Keyboard and clipboard workflow:** search focus and filter-clearing
  shortcuts plus rendered-copy actions for the editor and snippet context menu.
- **Clipboard capture:** create an unsaved multiline Unicode snippet directly
  from the toolbar, `Ctrl+Shift+N`, or the system-tray menu.
- **Global capture shortcut:** configurable Windows-wide clipboard capture that
  remains available while the window is hidden or expansion is paused.
- **Stability and diagnostics:** privacy-safe health reports, 10,000-snippet
  stress coverage, indexed matching, and frozen-EXE single-instance smoke tests.
- **Startup recovery:** SQLite integrity checks, newest-valid-backup recovery,
  quarantine of damaged database files, and configurable backup retention.
- **Bulk productivity:** extended row selection with transactional enable,
  disable, favorite, category, export, and delete operations plus searchable
  restore differences.
- **Accessible appearance:** light, dark, and high-contrast themes, accessible
  control names, keyboard shortcuts and tab order, and persisted window,
  splitter, and column state.
- **Quality and release automation:** linting, core type checking, coverage,
  dependency auditing, Dependabot, SBOM generation, tag-triggered releases,
  checksums, and portable executable smoke testing.
- **Delivery:** unit and simulated-engine tests, PyInstaller build script,
  Windows smoke test procedure, GitHub Actions tests, and Windows build artifact.

## Intentionally outside this edition

Cloud synchronization, shared team libraries, images, arbitrary scripts,
interactive forms, and macOS support are not planned for this private offline
edition. Keeping these features out preserves the application's local-only
privacy and compact scope.
