# QuickType 3.0.0 release notes

QuickType 3.0 introduces Rich Content while preserving Plain Text as the
default and keeping existing snippets unchanged.

## Highlights

- Visual and HTML editing with an automatically generated plain-text fallback.
- Local embedded images from files, clipboard, or drag-and-drop, including
  resize, alternative text, links, safe re-encoding, and SQLite storage.
- Atomic visual chips for all existing Smart Elements. No new macro kinds were
  added.
- Rich expansion through simultaneous Windows RTF, HTML, and plain-text
  clipboard formats with guarded restoration of the original clipboard.
- Nested Rich snippets preserve formatting and images; dynamic values are
  escaped before entering HTML.
- Sanitized HTML excludes scripts, events, forms, frames, global styles,
  unsafe URLs, remote images, and tables.
- SQLite schema 8 stores content format, canonical HTML, and transactional
  image assets. The first v3 migration creates
  `QuickType-before-v3-migration-*.sqlite3`.
- Portable `.qtbackup` packages contain a JSON manifest and deduplicated,
  checksum-verified assets. Legacy JSON v1/v2 backups remain importable.

## Compatibility and limits

- Windows 11 x64 remains the supported platform.
- QuickType 2.x cannot edit Rich snippets. Use the automatic pre-migration
  database copy if a downgrade is necessary.
- Each image is limited to 10 MiB, images in one snippet to 25 MiB, library
  assets to 250 MiB, and unpacked backup content to 500 MiB.
- Tables, remote images, new Smart Element kinds, scripts, AI, synchronization,
  and macOS are outside this release.

Before publication, complete the automated gates and the Rich Content Windows
application matrix in [TESTING.md](TESTING.md).
