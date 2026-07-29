# QuickType

[![Windows build](https://github.com/grzesiokk/QuickType-textexpander/actions/workflows/windows-build.yml/badge.svg)](https://github.com/grzesiokk/QuickType-textexpander/actions/workflows/windows-build.yml)

QuickType is a private, portable text expander for 64-bit Windows 11. It runs
locally, stores its SQLite database beside the executable, and does not require
Python on the target computer.

## Application preview

![QuickType running on Windows 11](docs/quicktype-screenshot.png)

The screenshot shows QuickType 1.25.0 with searchable snippets, categories,
usage statistics, application rules, bulk actions, backup tools, and a live
preview of dynamic variables.

## Ready-to-use application

**[Download the latest QuickType.exe](https://github.com/grzesiokk/QuickType-textexpander/releases/latest/download/QuickType.exe)**

Prebuilt Windows binaries are published under
[GitHub Releases](https://github.com/grzesiokk/QuickType-textexpander/releases). The
`dist` directory is intentionally not committed to Git. When building the
project locally, the resulting application is created at:

```text
dist\QuickType.exe
```

Copy `QuickType.exe` to a writable folder, then start it with a double-click.
On first launch it creates:

```text
QuickTypeData\quicktype.sqlite3
```

Keep `QuickType.exe` and `QuickTypeData` together when moving the application.
Close QuickType from its tray menu before copying its database.

## Using snippets

1. Click **New** / **Nowy**.
2. Enter an abbreviation without whitespace, for example `;sig`.
3. Optionally assign a category and target applications, then enter the
   expansion text and choose a trigger mode.
4. Save the snippet and try it in Notepad, Word, a browser, VS Code, or Windows
   Terminal.

Use **Duplicate** / **Duplikuj** or `Ctrl+D` to create an editable copy of the
selected snippet. Its abbreviation receives a unique `_copy` suffix.

Copy any text, then press the global `Ctrl+Alt+N` shortcut even while QuickType
is hidden. You can also choose **New from clipboard** / **Nowy ze schowka**
from the arrow beside **New**, press `Ctrl+Shift+N` while the main window is
active, or use the tray-menu action. QuickType opens a new unsaved snippet with
the clipboard text already placed in its expansion. Enter an abbreviation and
save it normally.

Right-click a snippet to enable or disable it, change its favorite status,
duplicate it, or delete it. Double-click the star or enabled-state column for a
quick toggle.

Click a list-column header to sort the library by favorite status, enabled
state, abbreviation, category, trigger mode, or numeric usage count. The label
above the table shows how many snippets match the current search and category
filters.

Use **Categories** / **Kategorie** to see how many snippets belong to each
category, rename a category across the whole library, or remove the category
assignment without deleting its snippets.

Open **Statistics** / **Statystyki** to see the total number of expansions, a
ranking of used snippets, and their most recent use. Counters can be reset for
the selected snippet or for the whole library after confirmation.

Trigger modes:

- **After delimiter** expands after Space, Tab, Enter, or common punctuation
  and replays the delimiter.
- **Immediate** expands as soon as the final abbreviation character is typed.

Supported variables:

| Variable | Result |
|---|---|
| `{{date}}` | Date as `DD.MM.YYYY` |
| `{{date:%Y-%m-%d}}` | Date with a Python `strftime` format |
| `{{time}}` | Time as `HH:MM` |
| `{{time:%H:%M:%S}}` | Time with a custom format |
| `{{clipboard}}` | Plain text currently in the clipboard |
| `{{cursor}}` | Final cursor position; may occur once |

The interface can be switched between Polish and English in Settings.
Closing the main window keeps QuickType in the system tray. The tray menu can
pause expansion, enable autostart, reopen the window, or quit completely.

## Quick access

Press **Ctrl+Alt+Space** in another application to open a searchable list of
enabled snippets. Search by abbreviation, category, or expansion text, then
press **Enter** to insert the selected snippet into the original window.
Template variables and the cursor marker are rendered in the same way as with
typed abbreviations. **Esc** closes the quick-access window without inserting.
Mark important snippets as favorites to keep them at the top of this list.
Other snippets are ordered by their usage count.

The global shortcut can be changed immediately in Settings to
**Ctrl+Shift+Space**, **Alt+Shift+Space**, or disabled completely.
The separate global clipboard-capture shortcut defaults to **Ctrl+Alt+N** and
can be changed to **Alt+Shift+N** or disabled. Both global shortcuts continue
to work when text expansion itself is paused.

## Keyboard and clipboard workflow

- `Ctrl+F` focuses the snippet search and selects its current query.
- `Esc` clears both the search query and category filter.
- `Ctrl+N` starts an empty snippet; `Ctrl+Shift+N` starts one from clipboard
  while the main window is active.
- `Ctrl+Alt+N` starts a snippet from clipboard globally, including while
  QuickType is hidden in the tray.
- `Ctrl+S` saves the current snippet.
- `Ctrl+Shift+C` copies the editor's rendered preview.
- **Copy result** / **Kopiuj wynik** is also available below the preview and in
  the snippet context menu.

Copied results render date, time, clipboard, and cursor variables in the same
way as expansion. `{{cursor}}` is removed because the clipboard contains plain
text and has no cursor position.

## Application-specific snippets

Enter executable names such as `Code.exe`, `WINWORD.EXE`, or `chrome.exe` in a
snippet's **Only in applications** field to restrict it to those programs.
Separate multiple names with commas. Leave the field empty to make the snippet
available everywhere. Application matching is case-insensitive and is applied
to both typed abbreviations and the quick-access picker.

## Backups, statistics, and exclusions

- Snippets can be organized into categories. Use the category selector above
  the list to filter the library; category names are preserved in backups.
- Category changes are applied transactionally to every affected snippet and
  trigger the same automatic backup protection as ordinary edits.
- Favorite status is shown with a star and is also preserved in backups.
- Per-snippet application rules are preserved in backups.
- **Export** creates a human-readable UTF-8 JSON backup containing all snippets
  and their usage statistics.
- Use the arrow beside **Export** and choose **Export visible** to save only the
  snippets shown by the current search and category filters.
- Automatic backups are enabled by default. QuickType writes them after snippet
  changes to `QuickTypeData\Backups`. Settings can disable them or retain
  between 1 and 200 automatic copies.
- **Backups** / **Kopie** opens a unified browser for automatic and manual
  backups, safety copies created before imports and restores, and other valid
  QuickType JSON backups stored in `QuickTypeData\Backups`.
- The browser shows each backup's type, date, snippet count, and file name. Use
  the type filter to narrow the list. Invalid or damaged JSON files are ignored.
- Selecting a backup shows a read-only restore impact summary: snippets that
  will be added, changed, removed, or left unchanged. The same counts are
  repeated in the final confirmation before any data is modified.
- The difference table can be filtered by action and compares the current and
  backup expansions. Changed snippets also identify the exact fields that
  differ, including trigger mode, category, application rules, and statistics.
- Use **Copy report** to place the complete localized difference report on the
  clipboard. The browser can also refresh the catalog, open the backup folder,
  and permanently delete a selected backup after explicit confirmation.
- Before restoring any listed backup, QuickType saves an additional
  `QuickType-before-restore-*.json` safety copy of the current state.
- If SQLite is damaged or fails its startup integrity check, QuickType offers
  to restore the newest valid JSON backup. The damaged database and sidecar
  files are quarantined instead of overwritten.
- **Import** first shows how many snippets are new and lists every abbreviation
  that conflicts with the current library. The conflict table compares the
  current expansion with the expansion stored in the backup.
- Choose **Merge** to add only new snippets, **Update** to add new snippets and
  overwrite only conflicts while preserving the rest of the library, or
  **Replace** to replace the full library.
- Before either import mode changes data, QuickType writes a timestamped
  `QuickType-before-import-*.json` safety copy of the current library to
  `QuickTypeData\Backups`.
- The snippet list shows how many times each abbreviation has expanded. The
  editor also shows the most recent use.
- Select multiple rows with Ctrl or Shift and use **Bulk** to enable, disable,
  favorite, recategorize, export, or delete selected snippets transactionally.
- Light, dark, and high-contrast themes are available in Settings. QuickType
  remembers the main-window geometry, column widths, and panel split.
- The statistics window excludes never-used snippets from the ranking while
  still including them in the library total.
- Settings contains an excluded-applications list. Enter one executable name
  per line, such as `KeePass.exe`, to prevent expansion in that process.

## Data maintenance

Open **Data** / **Dane** from the main toolbar to see the number of snippets,
the number of JSON backups, the SQLite database size, and the exact data-folder
path. From the same window you can:

- create a timestamped `QuickType-manual-*.json` backup immediately;
- run SQLite's integrity check without modifying the database;
- copy a privacy-safe diagnostic report containing health and counts but no
  abbreviations, expansion text, clipboard data, typed characters, categories,
  or application lists;
- open `QuickTypeData` directly in Windows Explorer.

## Safety and Windows limitations

- Typed text is held only in a short in-memory matching buffer and is never
  persisted or logged.
- Expansion is skipped when Windows UI Automation identifies the focused
  control as a password field.
- Windows blocks `SendInput` into applications running at a higher integrity
  level. An ordinary QuickType process therefore cannot expand text inside an
  application started as Administrator.
- A multiline snippet used in a terminal may execute commands. Review terminal
  snippets carefully.
- The personal build is unsigned, so Microsoft SmartScreen may show a warning.

## Development

Requirements:

- Windows 11 x64
- Python 3.12
- PowerShell

Create a virtual environment and install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[build,test,quality]"
```

Run from source:

```powershell
.\.venv\Scripts\python.exe -m quicktype
```

Run tests:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Run all local quality gates:

```powershell
.\.venv\Scripts\ruff.exe check src tests scripts
.\.venv\Scripts\mypy.exe src/quicktype/models.py src/quicktype/storage.py src/quicktype/backup.py src/quicktype/backup_catalog.py src/quicktype/auto_backup.py src/quicktype/matcher.py src/quicktype/diagnostics.py src/quicktype/importing.py src/quicktype/recovery.py src/quicktype/maintenance.py
.\.venv\Scripts\python.exe -m pytest --cov=quicktype --cov-fail-under=72
.\.venv\Scripts\pip-audit.exe --local --skip-editable
```

The [release verification checklist](docs/TESTING.md) covers automated and
manual Windows 11 checks. The [development status](ROADMAP.md) records the
completed phases and the deliberately excluded cloud features.

Tagged versions are tested, audited, smoke-tested, and published automatically
with `QuickType.exe`, `SHA256SUMS`, and a CycloneDX SBOM. See the
[compatibility record](docs/COMPATIBILITY.md) for automated coverage and the
target-application inventory.

Build the single-file, windowed executable:

```powershell
.\build.ps1
```

If Python is not on `PATH`, set `QUICKTYPE_PYTHON` to a Python 3.12 executable
before running `build.ps1`. Use `.\build.ps1 -SkipInstall` to reuse the current
virtual environment without reinstalling dependencies.

## Data and privacy

QuickType has no network integration, telemetry, synchronization, or cloud
storage. Snippets and settings remain in the local SQLite database.

The project is licensed under the MIT License. PySide6/Qt and other packaged
dependencies retain their respective upstream licenses.
