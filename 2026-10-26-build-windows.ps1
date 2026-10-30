$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Python = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } else { "python" }
$BuildVenv = Join-Path $Root "build\packaging-venv-windows"
$BrowserDir = Join-Path $Root "build\playwright-browsers"
$DistDir = Join-Path $Root "dist"
$WorkDir = Join-Path $Root "build\pyinstaller-work-windows"
$AppDir = Join-Path $DistDir "Canvas Lecture AI"
$Version = & $Python -c "import pathlib,tomllib; print(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['version'])"
$Machine = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
$Arch = if ($Machine -eq "Arm64") { "arm64" } else { "x64" }

function Assert-LastExitCode([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed with exit code $LASTEXITCODE." }
}

if (Test-Path $AppDir) {
    throw "$AppDir already exists. Move it aside before building so an earlier build is not overwritten."
}

New-Item -ItemType Directory -Force -Path (Join-Path $Root "build"), $DistDir | Out-Null
if (-not (Test-Path (Join-Path $BuildVenv "Scripts\python.exe"))) {
    & $Python -m venv $BuildVenv
    Assert-LastExitCode "Creating the packaging environment"
}
$VenvPython = Join-Path $BuildVenv "Scripts\python.exe"
$PyInstaller = Join-Path $BuildVenv "Scripts\pyinstaller.exe"

& $VenvPython -m pip install --upgrade pip
Assert-LastExitCode "Upgrading pip"
if ($Arch -eq "arm64") {
    & $VenvPython -m pip install -e "."
    Assert-LastExitCode "Installing ARM64 application dependencies"
    & $VenvPython -m pip install "pyinstaller>=6.16"
    Assert-LastExitCode "Installing PyInstaller"
} else {
    & $VenvPython -m pip install -e ".[ai,dev]"
    Assert-LastExitCode "Installing application dependencies"
}
$env:PLAYWRIGHT_BROWSERS_PATH = $BrowserDir
& $VenvPython -m playwright install chromium
Assert-LastExitCode "Installing Playwright Chromium"

$PlaywrightFfmpeg = Get-ChildItem $BrowserDir -Filter "ffmpeg*.exe" -File -Recurse |
    Select-Object -First 1
if (-not $PlaywrightFfmpeg) {
    throw "Playwright's FFmpeg helper was not found under $BrowserDir."
}
$env:IMAGEIO_FFMPEG_EXE = $PlaywrightFfmpeg.FullName

& $PyInstaller `
    --noconfirm `
    --distpath $DistDir `
    --workpath $WorkDir `
    (Join-Path $Root "2026-10-26-windows.spec")
Assert-LastExitCode "Packaging Canvas Lecture AI"

function Invoke-SignTool([string]$Path) {
    if (-not $env:WINDOWS_SIGNING_CERTIFICATE_PFX) { return }
    $Certificate = Join-Path $env:RUNNER_TEMP "canvas-lecture-ai-signing.pfx"
    [IO.File]::WriteAllBytes($Certificate, [Convert]::FromBase64String($env:WINDOWS_SIGNING_CERTIFICATE_PFX))
    $SignTool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Filter signtool.exe -Recurse |
        Sort-Object FullName -Descending | Select-Object -First 1
    if (-not $SignTool) { throw "signtool.exe was not found." }
    & $SignTool.FullName sign /fd SHA256 /td SHA256 /tr http://timestamp.digicert.com `
        /f $Certificate /p $env:WINDOWS_SIGNING_CERTIFICATE_PASSWORD $Path
    if ($LASTEXITCODE -ne 0) { throw "Signing failed for $Path" }
}

Invoke-SignTool (Join-Path $AppDir "Canvas Lecture AI.exe")

$IsccCommand = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
$IsccCandidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:ChocolateyToolsLocation\InnoSetup\ISCC.exe"
)
$Iscc = if ($IsccCommand) {
    $IsccCommand.Source
} else {
    $IsccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $Iscc) {
    throw "Inno Setup 6 is required. Install it from https://jrsoftware.org/isinfo.php."
}

& $Iscc "/DMyAppVersion=$Version" "/DMyAppArch=$Arch" (Join-Path $Root "2026-10-26-windows-installer.iss")
Assert-LastExitCode "Building the Windows installer"
$Installer = Join-Path $DistDir "Canvas-Lecture-AI-v$Version-windows-$Arch-setup.exe"
Invoke-SignTool $Installer
$Hash = (Get-FileHash -Algorithm SHA256 $Installer).Hash.ToLowerInvariant()
"$Hash  $(Split-Path -Leaf $Installer)" | Set-Content -Encoding ascii "$Installer.sha256"
Write-Host "Built $Installer"
