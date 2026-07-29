# Windows 11 compatibility verification

## Automated coverage

Every pull request and `main` build now performs:

- simulated keyboard-engine tests for immediate and delimiter expansion,
  modifiers, Unicode, cursor restoration, application exclusions, injected
  event filtering, and password-field decisions;
- a 10,000-snippet matcher stress test;
- a frozen `QuickType.exe` smoke test that creates a fresh portable database
  and verifies that a second launch does not leave another instance running;
- lint, core static-type checks, at least 72% overall statement coverage, and a
  dependency vulnerability audit.

Run the local application inventory with:

```powershell
.\scripts\compatibility_inventory.ps1
```

## Inventory on the release workstation

Checked on 29 July 2026:

| Application | Detected |
|---|---|
| Notepad | yes |
| Microsoft Word | no |
| Microsoft Outlook | no |
| Google Chrome | yes |
| Microsoft Edge | yes |
| Visual Studio Code | no |
| Windows Terminal | yes |

Word, Outlook, and Visual Studio Code cannot be interactively verified on this
workstation because they are not installed.

## Physical-keyboard acceptance

QuickType deliberately ignores Windows events marked as injected. This prevents
recursive expansion and means a synthetic UI driver cannot honestly reproduce
the physical-keyboard acceptance test. The checklist in `TESTING.md` remains
the authoritative procedure on a workstation where the target applications are
installed. This limitation does not affect the automated engine, storage, UI,
portable-build, or single-instance tests.
