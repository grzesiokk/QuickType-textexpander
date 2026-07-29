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
7. Edit, duplicate, delete, export, and import snippets. Verify the right-click
   actions and double-click toggles in the snippet list.
8. Sort each list column, especially usage values such as 2 and 10. Combine
   search and category filters, verify the visible count, then export only the
   visible snippets and import that file into a separate test library.
9. Verify `Ctrl+F`, clear search and category filters with `Esc`, then copy a
   rendered preview containing date, clipboard, and cursor variables with
   `Ctrl+Shift+C` and the context-menu action.
10. Rename and remove a category in the category manager. Confirm that all
   affected snippets remain present and that the category filters refresh.
11. Open Statistics, verify the ranking and dates, then reset one counter and
   all remaining counters after their confirmation prompts.
12. Open Data maintenance, create a manual backup, run the database integrity
   check, and open the data folder in Windows Explorer.
13. Restore an automatic backup and confirm that the previous state is saved as
   `QuickType-before-restore-*.json`.
14. Confirm that `QuickTypeData\Backups` retains no more than 20 automatic
   backup files.
15. Confirm that expansion does not run in QuickType's editor or a recognized
   password field.
16. Close QuickType from the tray and start it again to confirm data
    persistence.
