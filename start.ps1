param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,
    [switch]$ForceInstall
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $Root "backend"
$FrontendDir = Join-Path $Root "frontend"
$TmpDir = Join-Path $Root "tmp"
$BackendPidFile = Join-Path $TmpDir "backend.pid"
$FrontendPidFile = Join-Path $TmpDir "frontend.pid"

function Ensure-Directory($Path) {
    if (-not (Test-Path $Path)) {
        New-Item -ItemType Directory -Path $Path | Out-Null
    }
}

function Test-HttpReady($Url) {
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    }
    catch {
        return $false
    }
}

function Wait-HttpReady($Url, $Name) {
    for ($i = 0; $i -lt 60; $i++) {
        if (Test-HttpReady $Url) {
            Write-Host "$Name is ready: $Url"
            return
        }
        Start-Sleep -Seconds 1
    }
    Write-Warning "$Name did not respond yet. Check logs in $TmpDir."
}

function Test-ProcessFromPidFile($PidFile) {
    if (-not (Test-Path $PidFile)) {
        return $false
    }

    $processIdText = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if (-not $processIdText) {
        return $false
    }

    $process = Get-Process -Id ([int]$processIdText) -ErrorAction SilentlyContinue
    return $null -ne $process
}

function Require-Command($Name, $InstallHint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name was not found. $InstallHint"
    }
}

function Test-BackendInstalled($Python) {
    & $Python -m pip show vocabulary-learning-backend *> $null
    return $LASTEXITCODE -eq 0
}

Ensure-Directory $TmpDir

$Python = Join-Path $BackendDir ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Require-Command "python" "Install Python 3.11 or newer, then re-run this script."
    Write-Host "Creating backend virtual environment..."
    Push-Location $BackendDir
    try {
        python -m venv .venv
    }
    finally {
        Pop-Location
    }
}

if ($ForceInstall -or -not (Test-BackendInstalled $Python)) {
    Write-Host "Installing backend dependencies..."
    Push-Location $BackendDir
    try {
        & $Python -m pip install -e .
        if ($LASTEXITCODE -ne 0) {
            throw "Backend dependency installation failed."
        }
    }
    finally {
        Pop-Location
    }
}
else {
    Write-Host "Backend dependencies already installed. Use -ForceInstall to reinstall."
}

Require-Command "pnpm" "Install pnpm, then re-run this script."
if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
    Write-Host "Installing frontend dependencies..."
    Push-Location $FrontendDir
    try {
        pnpm install
        if ($LASTEXITCODE -ne 0) {
            throw "Frontend dependency installation failed."
        }
    }
    finally {
        Pop-Location
    }
}

if (Test-ProcessFromPidFile $BackendPidFile) {
    Write-Host "Backend is already running from $BackendPidFile."
}
else {
    Write-Host "Starting backend on http://127.0.0.1:$BackendPort ..."
    $backendCommand = "Set-Location '$BackendDir'; `$env:VOCAB_ENRICHMENT_SOURCE='fallback'; & '$Python' -m uvicorn app.main:app --reload --reload-dir app --host 127.0.0.1 --port $BackendPort"
    $backendProcess = Start-Process powershell -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $TmpDir "backend.log") `
        -RedirectStandardError (Join-Path $TmpDir "backend.err.log") `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $backendCommand)
    Set-Content -Path $BackendPidFile -Value $backendProcess.Id
}

if (Test-ProcessFromPidFile $FrontendPidFile) {
    Write-Host "Frontend is already running from $FrontendPidFile."
}
else {
    Write-Host "Starting frontend on http://127.0.0.1:$FrontendPort ..."
    $frontendCommand = "Set-Location '$FrontendDir'; pnpm exec vite --host 127.0.0.1 --port $FrontendPort"
    $frontendProcess = Start-Process powershell -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $TmpDir "frontend.log") `
        -RedirectStandardError (Join-Path $TmpDir "frontend.err.log") `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $frontendCommand)
    Set-Content -Path $FrontendPidFile -Value $frontendProcess.Id
}

$BackendUrl = "http://127.0.0.1:$BackendPort"
$FrontendUrl = "http://127.0.0.1:$FrontendPort"

Wait-HttpReady "$BackendUrl/api/health" "Backend"
Wait-HttpReady $FrontendUrl "Frontend"

Write-Host ""
Write-Host "VocabularyLearning is running."
Write-Host "Open: $FrontendUrl"
Write-Host "API:  $BackendUrl/api/health"
Write-Host "Logs: $TmpDir"
Write-Host "Stop: .\stop.ps1"
