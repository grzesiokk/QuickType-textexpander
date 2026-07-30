# QuickType 2.1.0 release notes

## Highlights

- Optional session-only clipboard history, disabled by default.
- `clip:` and `schowek:` scopes in Quick Access for searching clipboard text.
- Direct clipboard insertion guarded by the existing excluded-application and
  password-field checks.
- Safe template transforms: `upper`, `lower`, `title`, `trim`, and `default`.
- No database schema or backup-format migration.

## Privacy and upgrade

Clipboard history is kept only in memory, limited to 50 text entries and 1 MiB,
and is cleared when QuickType exits or when the user disables or clears it. It
is not included in SQLite data, JSON backups, exports, or diagnostic reports.
The feature is disabled by default after upgrading.

Existing snippets, settings, backups, and imports remain compatible with the
2.0 format. The only new persisted value is the opt-in
`clipboard_history_enabled` setting.

## Template transforms

Use a source-qualified filter such as `{{upper:var:name}}`,
`{{lower:clipboard}}`, `{{title:match:1}}`, `{{trim:var:company}}`, or
`{{default:var:name|fallback}}`. Nested tokens are intentionally not supported
in this release.

## Verification

The release must pass the automated test suite, Ruff, mypy, coverage threshold,
dependency audit, PyInstaller build, and portable executable smoke test. The
manual checklist in `docs/TESTING.md` covers clipboard-history privacy,
picker insertion, and template-transform behavior.
