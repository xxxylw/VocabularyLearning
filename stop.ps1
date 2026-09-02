$ErrorActionPreference = "Continue"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$TmpDir = Join-Path $Root "tmp"
$PidFiles = @(
    (Join-Path $TmpDir "frontend.pid"),
    (Join-Path $TmpDir "backend.pid")
)

foreach ($pidFile in $PidFiles) {
    if (-not (Test-Path $pidFile)) {
        continue
    }

    $processIdText = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($processIdText) {
        $processId = [int]$processIdText
        $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($process) {
            Write-Host "Stopping process $processId from $pidFile ..."
            taskkill /PID $processId /T /F | Out-Null
        }
    }

    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

Write-Host "Stopped VocabularyLearning services started by start.ps1."
