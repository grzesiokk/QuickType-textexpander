param(
    [string]$Executable = ".\dist\QuickType.exe"
)

$ErrorActionPreference = "Stop"
$ResolvedExecutable = (Resolve-Path -LiteralPath $Executable).Path
$SmokeBase = $env:RUNNER_TEMP
if (-not $SmokeBase) {
    $SmokeBase = [System.IO.Path]::GetTempPath()
}
$SmokeRoot = Join-Path $SmokeBase (
    "QuickType-smoke-" + [guid]::NewGuid().ToString("N")
)
New-Item -ItemType Directory -Path $SmokeRoot | Out-Null
$env:QUICKTYPE_DATA_DIR = $SmokeRoot

try {
    Start-Process -FilePath $ResolvedExecutable -ArgumentList "--minimized"
    Start-Sleep -Seconds 8
    $FirstProcesses = @(
        Get-Process -Name "QuickType" -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -eq $ResolvedExecutable }
    )
    if ($FirstProcesses.Count -lt 1) {
        throw "QuickType.exe did not stay running."
    }
    $Database = Join-Path $SmokeRoot "quicktype.sqlite3"
    if (-not (Test-Path -LiteralPath $Database)) {
        throw "QuickType.exe did not create the portable database."
    }

    Start-Process -FilePath $ResolvedExecutable -ArgumentList "--minimized"
    $SecondProcesses = @()
    $SecondInstanceTimeout = [System.Diagnostics.Stopwatch]::StartNew()
    do {
        Start-Sleep -Seconds 1
        $SecondProcesses = @(
            Get-Process -Name "QuickType" -ErrorAction SilentlyContinue |
                Where-Object { $_.Path -eq $ResolvedExecutable }
        )
    } while (
        $SecondProcesses.Count -gt $FirstProcesses.Count -and
        $SecondInstanceTimeout.Elapsed.TotalSeconds -lt 30
    )
    if ($SecondProcesses.Count -ne $FirstProcesses.Count) {
        $ProcessIds = ($SecondProcesses.Id | Sort-Object) -join ", "
        throw (
            "A second QuickType instance remained running. " +
            "Expected $($FirstProcesses.Count) process(es), found " +
            "$($SecondProcesses.Count); PIDs: $ProcessIds."
        )
    }
    Write-Host "Portable database and single-instance smoke test passed."
}
finally {
    Get-Process -Name "QuickType" -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $ResolvedExecutable } |
        Stop-Process -Force
    Remove-Item Env:QUICKTYPE_DATA_DIR -ErrorAction SilentlyContinue
}
