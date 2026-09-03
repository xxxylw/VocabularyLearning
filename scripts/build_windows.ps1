# Build the Windows local package (P0-5) - run on a Windows machine.
#
# Produces dist/VocabularyLearning-v<Version>-win64.zip + dist/checksums.txt
# from a clean repo checkout. Prerequisites: Python 3.11+, Node.js, pnpm.
#
# Usage (repo root, PowerShell):
#   .\scripts\build_windows.ps1                  # full build with tests
#   .\scripts\build_windows.ps1 -SkipTests       # skip pytest
#   .\scripts\build_windows.ps1 -Version 1.0.0
param(
    [string]$Version = "1.0.0",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$BackendDir = Join-Path $Repo "backend"
$FrontendDir = Join-Path $Repo "frontend"
$BuildDir = Join-Path $Repo "build"
$PackageDir = Join-Path $BuildDir "pkg"
$AppRoot = Join-Path $PackageDir "VocabularyLearning"
$PyiWorkDir = Join-Path $BuildDir "pyi"
$DistDir = Join-Path $Repo "dist"
$VenvPython = Join-Path $BuildDir "venv\Scripts\python.exe"

function Require-Command($Name, $Hint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name not found. $Hint"
    }
}

Write-Host "== VocabularyLearning Windows build v$Version =="

Require-Command "python" "Install Python 3.11+ and re-run."
Require-Command "pnpm" "Install pnpm (npm i -g pnpm) and re-run."

# ---------------------------------------------------------------- clean
foreach ($dir in @($BuildDir, $DistDir)) {
    if (Test-Path $dir) { Remove-Item $dir -Recurse -Force }
}
New-Item -ItemType Directory -Path $AppRoot, $DistDir, $PyiWorkDir -Force | Out-Null

# ---------------------------------------------------------------- venv
if (-not (Test-Path $VenvPython)) {
    Write-Host "== creating build venv =="
    & python -m venv (Join-Path $BuildDir "venv")
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed." }
}
Write-Host "== installing backend + launcher deps =="
& $VenvPython -m pip install --upgrade pip --quiet
& $VenvPython -m pip install -e $BackendDir --quiet
& $VenvPython -m pip install -r (Join-Path $Repo "launcher\requirements.txt") --quiet
& $VenvPython -m pip install "pytest>=8.2" "httpx>=0.27" --quiet
& $VenvPython -m pip install "pyinstaller>=6.6" --quiet
if ($LASTEXITCODE -ne 0) { throw "dependency installation failed." }

# ---------------------------------------------------------------- tests
if (-not $SkipTests) {
    Write-Host "== backend tests =="
    & $VenvPython -m pytest (Join-Path $BackendDir "tests") -q
    if ($LASTEXITCODE -ne 0) { throw "backend tests failed." }

    Write-Host "== launcher tests =="
    Push-Location $Repo
    try {
        & $VenvPython -m pytest (Join-Path $Repo "launcher\tests") -q
        if ($LASTEXITCODE -ne 0) { throw "launcher tests failed." }
    }
    finally { Pop-Location }
}

# ---------------------------------------------------------------- frontend
Write-Host "== building frontend (vite) =="
Push-Location $FrontendDir
try {
    & pnpm install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) { throw "pnpm install failed." }
    & pnpm build
    if ($LASTEXITCODE -ne 0) { throw "frontend build failed." }
}
finally { Pop-Location }

$StaticDir = Join-Path $AppRoot "static"
New-Item -ItemType Directory -Path $StaticDir -Force | Out-Null
Copy-Item -Path (Join-Path $FrontendDir "dist\*") -Destination $StaticDir -Recurse -Force
if (-not (Test-Path (Join-Path $StaticDir "index.html"))) {
    throw "frontend build output missing index.html"
}

# ---------------------------------------------------------------- builtin db
Write-Host "== sanitizing builtin library =="
& $VenvPython (Join-Path $Repo "scripts\make_builtin_db.py") `
    --source (Join-Path $BackendDir "data\vocabulary.sqlite") `
    --output (Join-Path $AppRoot "builtin\vocabulary.sqlite") `
    --min-words 3383 --min-entries 8000 --min-examples 9000 --min-ready-pron 3000
if ($LASTEXITCODE -ne 0) {
    throw "builtin library verification failed - the source DB lacks full enrichment data."
}

# ---------------------------------------------------------------- pyinstaller
Write-Host "== building launcher executable (pyinstaller) =="
& $VenvPython -m PyInstaller `
    --noconfirm --clean --noconsole `
    --name "VocabularyLearning" `
    --distpath $PackageDir `
    --workpath $PyiWorkDir `
    --specpath $BuildDir `
    --paths $BackendDir `
    --paths $Repo `
    --collect-submodules app `
    --collect-submodules uvicorn `
    (Join-Path $Repo "launcher\__main__.py")
if ($LASTEXITCODE -ne 0) { throw "pyinstaller build failed." }
if (-not (Test-Path (Join-Path $AppRoot "VocabularyLearning.exe"))) {
    throw "launcher exe not produced at $AppRoot"
}

# ---------------------------------------------------------------- package extras
$readme = Get-Content (Join-Path $Repo "scripts\package_readme.txt") -Raw -Encoding UTF8
Set-Content -Path (Join-Path $AppRoot "README.txt") -Value $readme.Replace("{VERSION}", $Version) -Encoding UTF8

# ---------------------------------------------------------------- zip + checksum
$ZipName = "VocabularyLearning-v$Version-win64.zip"
$ZipPath = Join-Path $DistDir $ZipName
Write-Host "== packing $ZipName =="
Compress-Archive -Path $AppRoot -DestinationPath $ZipPath -CompressionLevel Optimal

$hash = (Get-FileHash -Path $ZipPath -Algorithm SHA256).Hash
Set-Content -Path (Join-Path $DistDir "checksums.txt") -Value "$hash  $ZipName" -Encoding ascii

if (Test-Path (Join-Path $Repo "docs\release_notes_v$Version.md")) {
    Copy-Item (Join-Path $Repo "docs\release_notes_v$Version.md") $DistDir
}

Write-Host ""
Write-Host "== BUILD DONE =="
Get-ChildItem $DistDir | Format-Table Name, Length
Write-Host "SHA256: $hash"
Write-Host ""
Write-Host "Next: create the GitHub release, e.g."
Write-Host "  gh release create v$Version $ZipPath $DistDir\checksums.txt --title 'v$Version' --notes-file docs\release_notes_v$Version.md"
