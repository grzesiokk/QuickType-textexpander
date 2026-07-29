param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Executable = Join-Path $ProjectRoot "dist\QuickType.exe"
$BuildPython = $VenvPython

if (-not (Test-Path -LiteralPath $VenvPython)) {
    $BootstrapPython = $env:QUICKTYPE_PYTHON
    if (-not $BootstrapPython) {
        $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($PythonCommand) {
            $BootstrapPython = $PythonCommand.Source
        }
    }
    if (-not $BootstrapPython) {
        throw "Python 3.12 is required to create .venv. Set QUICKTYPE_PYTHON to python.exe."
    }
    if ($SkipInstall) {
        $BuildPython = $BootstrapPython
    }
    else {
        & $BootstrapPython -m venv (Join-Path $ProjectRoot ".venv")
        if ($LASTEXITCODE -ne 0) {
            throw "Creating the virtual environment failed with exit code $LASTEXITCODE."
        }
    }
}

if (-not $SkipInstall) {
    & $BuildPython -m pip install -e "$ProjectRoot[build,test]"
    if ($LASTEXITCODE -ne 0) {
        throw "Installing dependencies failed with exit code $LASTEXITCODE."
    }
}

& $BuildPython (Join-Path $ProjectRoot "scripts\generate_icon.py")
if ($LASTEXITCODE -ne 0) {
    throw "Generating the application icon failed with exit code $LASTEXITCODE."
}

$RunningBuild = Get-Process -Name "QuickType" -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -eq $Executable }
if ($RunningBuild) {
    throw "Close the running dist\QuickType.exe from its tray menu before rebuilding."
}

& $BuildPython -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath (Join-Path $ProjectRoot "dist") `
    --workpath (Join-Path $ProjectRoot "build\pyinstaller") `
    (Join-Path $ProjectRoot "packaging\quicktype.spec")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE."
}

if (-not (Test-Path -LiteralPath $Executable)) {
    throw "Build finished without dist\QuickType.exe."
}

Write-Host ""
Write-Host "Built: $Executable"
