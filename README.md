# QuickType

QuickType is a private, portable text expander for 64-bit Windows 11. It runs
locally, stores its SQLite database beside the executable, and does not require
Python on the target computer.

## Ready-to-use application

The built application is located at:

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
3. Enter the expansion text and choose a trigger mode.
4. Save the snippet and try it in Notepad, Word, a browser, VS Code, or Windows
   Terminal.

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

## Backups, statistics, and exclusions

- **Export** creates a human-readable UTF-8 JSON backup containing all snippets
  and their usage statistics.
- **Import** can replace the current library or merge a backup while skipping
  abbreviations that already exist.
- The snippet list shows how many times each abbreviation has expanded. The
  editor also shows the most recent use.
- Settings contains an excluded-applications list. Enter one executable name
  per line, such as `KeePass.exe`, to prevent expansion in that process.

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
.\.venv\Scripts\python.exe -m pip install -e ".[build,test]"
```

Run from source:

```powershell
.\.venv\Scripts\python.exe -m quicktype
```

Run tests:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

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
