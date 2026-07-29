$Targets = @(
    @{ Name = "Notepad"; Executable = "notepad.exe" },
    @{ Name = "Microsoft Word"; Executable = "WINWORD.EXE" },
    @{ Name = "Microsoft Outlook"; Executable = "OUTLOOK.EXE" },
    @{ Name = "Google Chrome"; Executable = "chrome.exe" },
    @{ Name = "Microsoft Edge"; Executable = "msedge.exe" },
    @{ Name = "Visual Studio Code"; Executable = "Code.exe" },
    @{ Name = "Windows Terminal"; Executable = "wt.exe" }
)
$RegistryRoots = @(
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths",
    "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths",
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths"
)

Write-Output "| Application | Executable | Detected |"
Write-Output "|---|---|---|"
foreach ($Target in $Targets) {
    $Paths = @()
    $Command = Get-Command $Target.Executable -ErrorAction SilentlyContinue
    if ($Command) {
        $Paths += $Command.Source
    }
    foreach ($Root in $RegistryRoots) {
        $Item = Get-ItemProperty -LiteralPath (
            Join-Path $Root $Target.Executable
        ) -ErrorAction SilentlyContinue
        if ($Item) {
            $Paths += $Item."(default)"
        }
    }
    $Detected = if ($Paths.Count) { "yes" } else { "no" }
    Write-Output (
        "| {0} | `{1}` | {2} |" -f
        $Target.Name,
        $Target.Executable,
        $Detected
    )
}
