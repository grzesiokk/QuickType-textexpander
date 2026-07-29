# QuickType release verification

## Automated

Run the complete test suite:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Build the portable executable:

```powershell
.\build.ps1 -SkipInstall
```

GitHub Actions repeats both operations on a clean Windows runner and publishes
`QuickType.exe` as a workflow artifact for 14 days.

## Manual Windows 11 checklist

Before publishing a release:

1. Start `dist\QuickType.exe` from a writable folder and confirm that
   `QuickTypeData\quicktype.sqlite3` is created.
2. Test immediate and delimiter snippets in Notepad.
3. Test multiline text, Polish characters, clipboard, date, time, and
   `{{cursor}}`.
4. Test the quick-access shortcut in Word or a browser.
5. Confirm application-specific snippets and excluded applications.
6. Confirm pause/resume, tray reopening, single instance, and optional
   autostart.
7. Edit, duplicate, delete, and export snippets. Verify the right-click actions
   and double-click toggles in the snippet list.
8. Import a backup containing both new and conflicting abbreviations. Verify the
   preview counts and side-by-side current/imported expansions. Test merge,
   update-conflicts, and full replace modes; confirm unrelated local snippets
   survive update mode and a `QuickType-before-import-*.json` safety copy is
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
   check, and open the data folder in Windows Explorer.
16. Open Backups and verify automatic, manual, before-import, and
   before-restore entries show the correct type, date, file name, and snippet
   count. Filter every type and verify that selecting a backup shows accurate
   added, changed, removed, and unchanged counts. Confirm that the same impact
   appears in the final prompt. Filter the per-snippet difference table, inspect
   the changed-field names and both expansion columns, and copy the report to
   the clipboard. Refresh the catalog, open its folder, and delete a disposable
   backup after confirmation. Restore a safety copy and verify the previous
   state is saved as a new `QuickType-before-restore-*.json`.
17. Confirm that `QuickTypeData\Backups` retains no more than 20 automatic
   backup files.
18. Confirm that expansion does not run in QuickType's editor or a recognized
   password field.
19. Close QuickType from the tray and start it again to confirm data
    persistence.
