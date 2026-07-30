# QuickType 2.0.0 release notes

## Highlights

- Built-in, opt-in Polish autocorrect, postal-code, emoji, national-flag, and
  inline-calculation libraries.
- Virtual quick search across user snippets and enabled catalogs, with
  multi-word queries, quoted phrases, Polish-diacritic normalization, source
  scopes, recent items, and a 200-result limit.
- One-window expansion forms, session-only remembered values, decimal
  calculations without `eval`, and composition of enabled snippets.
- Literal and bounded regex snippet kinds with numbered and named match groups,
  explicit priority, a 256-character buffer, and per-match timeout.
- SQLite schema 7 and backup format 2, preserving library settings, per-item
  exceptions, and usage while continuing to import format 1 backups.
- A reorganized menu bar and a compact toolbar for frequent actions.

All built-in libraries and the inline calculator remain disabled until the
user enables them. User data and remembered form values stay on the computer.

## Bundled data

The catalogs were generated on 2026-07-30. Source inputs are pinned by SHA-256
in `scripts/build_builtin_data.py`.

- Unicode Emoji 17.0 and CLDR 48.0.0, under the Unicode License v3.
- GeoNames Polish postal-code dump, under CC BY 4.0. Postal results are
  approximate and are not an official Polish Post register.
- Polish LanguageTool resources at commit
  `517f7ad765ee8bf92e90e3d3b872bfd82690c65b`, with the notices described in
  the in-app **Data sources and licenses** view.

The complete notices are bundled in
`src/quicktype/resources/DATA_LICENSES.md`.

## Upgrade

Starting 2.0.0 migrates an existing database in place without changing current
snippets. Automatic and manual full backups include built-in-library settings;
exports of selected or visible rows still contain only user snippets.

Before evaluating the release candidate, keep a copy of the existing
`QuickTypeData` directory and close every older QuickType process.
