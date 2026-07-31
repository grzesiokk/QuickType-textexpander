# QuickType release verification

## Automated

Run the complete test suite:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Run the same quality gates used by CI:

```powershell
.\.venv\Scripts\ruff.exe check src tests scripts
.\.venv\Scripts\mypy.exe src/quicktype/models.py src/quicktype/storage.py src/quicktype/backup.py src/quicktype/backup_catalog.py src/quicktype/auto_backup.py src/quicktype/matcher.py src/quicktype/template_engine.py src/quicktype/rich_content.py src/quicktype/clipboard_paste.py src/quicktype/builtin_libraries.py src/quicktype/search.py src/quicktype/diagnostics.py src/quicktype/importing.py src/quicktype/recovery.py src/quicktype/maintenance.py
.\.venv\Scripts\python.exe -m pytest --cov=quicktype --cov-report=term --cov-fail-under=72
.\.venv\Scripts\pip-audit.exe --local --skip-editable --progress-spinner off
```

Build the portable executable:

```powershell
.\build.ps1 -SkipInstall
```

GitHub Actions repeats the quality gates and build on a clean Windows runner
and publishes the executable, coverage, and SBOM artifacts for 14 days. Tagged
builds additionally publish a GitHub Release with `SHA256SUMS`.

## Manual Windows 11 checklist

Before publishing a release:

1. Start `dist\QuickType.exe` from a writable folder and confirm that
   `QuickTypeData\quicktype.sqlite3` is created.
2. Test immediate and delimiter snippets in Notepad.
3. Test multiline text, Polish characters, clipboard, date, time,
   `{{cursor}}`, one-window forms, choice and check fields, calculations, and
   composed snippets. Cancel a form and confirm the document is unchanged.
4. Test the quick-access shortcut in Word or a browser. Verify multi-word and
   quoted queries, Polish-diacritic normalization, `.`, `emoji:`, `flaga:`,
   and `kod:`. Confirm that no more than 200 rows are shown.
5. Confirm application-specific snippets and excluded applications.
6. Confirm pause/resume, tray reopening, single instance, and optional
   autostart.
7. Edit, duplicate, delete, and export snippets. Verify the right-click actions
   and double-click toggles in the snippet list.
   Select multiple rows with Ctrl and Shift, then verify every Bulk action.
8. Import a backup containing both new and conflicting abbreviations. Verify the
   preview counts and side-by-side current/imported expansions. Test merge,
   update-conflicts, and full replace modes; confirm unrelated local snippets
   survive update mode and a `QuickType-before-import-*.qtbackup` safety copy is
   created each time.
9. Sort each list column, especially usage values such as 2 and 10. Combine
   search and category filters, verify the visible count, then export only the
   visible snippets and import that file into a separate test library.
10. Copy multiline text containing Polish characters. Create a snippet from the
   New-button menu, `Ctrl+Shift+N`, and the tray action; confirm the expansion
   is prefilled, remains unsaved until an abbreviation is entered, and an empty
   clipboard does not replace the current edit.
11. Hide QuickType and use global `Ctrl+Alt+N` from another application. Repeat
    while expansion is paused, change the shortcut to `Alt+Shift+N`, then
    disable it in Settings and verify each state without restarting.
12. Verify `Ctrl+F`, clear search and category filters with `Esc`, then copy a
   rendered preview containing date, clipboard, and cursor variables with
   `Ctrl+Shift+C` and the context-menu action.
13. Rename and remove a category in the category manager. Confirm that all
   affected snippets remain present and that the category filters refresh.
14. Open Statistics, verify the ranking and dates, then reset one counter and
   all remaining counters after their confirmation prompts.
15. Open Data maintenance, create a manual backup, run the database integrity
   check, copy the privacy-safe diagnostic report, and open the data folder.
16. Open Backups and verify automatic, manual, before-import, and
   before-restore entries show the correct type, date, file name, and snippet
   count. Filter every type and verify that selecting a backup shows accurate
   added, changed, removed, and unchanged counts. Confirm that the same impact
   appears in the final prompt. Filter the per-snippet difference table, inspect
   the changed-field names and both expansion columns, and copy the report to
   the clipboard. Refresh the catalog, open its folder, and delete a disposable
   backup after confirmation. Restore a safety copy and verify the previous
   state is saved as a new `QuickType-before-restore-*.qtbackup`.
17. Confirm the default retention of 20 automatic backups. Change retention to
   3 and 30 and confirm pruning and persistence.
18. Confirm that expansion does not run in QuickType's editor or a recognized
   password field.
19. Close QuickType from the tray and start it again to confirm data
    persistence.
20. Switch between light, dark, and high-contrast themes. Resize the window,
    columns, and panel split, restart, and verify the restored layout. Complete
    the main workflow using only the keyboard.
21. On disposable data, corrupt a copy of `quicktype.sqlite3`, start QuickType,
    accept recovery, and verify that the newest valid backup is restored
    while the damaged database is preserved under a `*-corrupt-*` name.
22. In Libraries, verify every catalog is disabled on a fresh database. Enable
    both autocorrect profiles and test lower case, first-letter capitalization,
    ALL CAPS, a disabled exception, a word boundary, and Polish diacritics.
23. Search postal codes by code, locality, county, and voivodeship. Confirm the
    inserted value contains only `NN-NNN` and the result preview identifies the
    locality and region.
24. Insert emoji and national flags by direct abbreviation and Quick Search.
    Change a prefix, verify conflicting prefixes are rejected, disable one
    item, and copy another item into My snippets.
25. Test a valid regex rule with numbered and named groups, competing regex
    priorities, literal precedence, an invalid pattern, an invalid group
    reference, and a pattern that reaches the match timeout.
26. Enable inline calculations and test `10+5=?`, decimal input, division by
    zero, excessive exponentiation, and an invalid expression.
27. Open Help → Data sources and licenses and verify the bundled Unicode,
    GeoNames, and LanguageTool notices.
28. On a fresh database, confirm Clipboard history is disabled. Enable it,
    copy Unicode and multiline text in another application, open Quick Access,
    and verify that `clip:` and `schowek:` show only matching history entries.
29. Select a clipboard-history entry and press Enter. Confirm it inserts into
    the original application, does not change snippet usage statistics, and is
    blocked in an excluded application or recognized password field.
30. Copy more than 50 distinct entries and more than 1 MiB of text. Confirm
    the oldest entries are evicted, oversized entries are ignored, duplicate
    text moves to the top, and ordinary Quick Access search does not show
    clipboard history.
31. Clear clipboard history, disable it, and restart QuickType. Confirm no
    clipboard entries remain and no clipboard text appears in backups,
    exports, or diagnostic reports.
32. Create snippets using `upper`, `lower`, `title`, `trim`, and `default`
    filters for form values, clipboard text, and regex groups. Verify Unicode,
    empty/missing fallbacks, escaped pipes, preview output, and malformed-token
    validation in both Polish and English.
33. Create a Plain snippet and verify Smart Element chips for date, time,
    clipboard, cursor, input, choice, checkbox, variable, calculation, nested
    snippet, regex group, and transforms. Insert each from the palette, edit
    with Enter or double click, remove atomically with Backspace/Delete, and
    verify the `{{...}}` round trip.
34. Convert Plain to Rich and verify the Visual, HTML, and read-only Plain
    fallback tabs remain synchronized. Apply valid HTML, reject invalid or
    unsafe HTML, then convert back to Plain after confirmation.
35. In the Visual editor, verify undo/redo, bold, italic, underline, strike,
    font family and size, text/background colors, alignment, lists, links, and
    keyboard-only access in Polish and English across all three themes.
36. Add PNG, JPEG, GIF/WebP, and clipboard images; test drag-and-drop, width
    changes preserving aspect ratio, alternative text, optional link, and
    deletion. Move the source files and confirm saved images still render.
37. Export Rich snippets to `.qtbackup`, import them into a fresh library, and
    verify formatting and images. Also import JSON v1/v2 as Plain. Corrupt an
    asset checksum and confirm import is rejected without changing the library.
38. Verify Rich expansion publishes formatting, a link, an inline image, and a
    Smart Element in Word, Outlook desktop, Gmail, and Outlook Web using current
    Edge and Chrome. Notepad and ordinary HTML text fields must receive the
    readable Plain fallback.
39. Verify `{{clipboard}}` reads the pre-expansion value, internal clipboard
    writes do not enter QuickType history, the original clipboard returns after
    paste, and an intervening external clipboard change prevents restoration.
    Repeat cancellation/error recovery, excluded application, password field,
    active-window change, nested Rich/Plain snippets, cycles, and cursor
    placement. Confirm validation rejects an image after `{{cursor}}`.
