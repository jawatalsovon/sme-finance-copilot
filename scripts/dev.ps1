# Dev helper for Windows PowerShell 5.1+.
# Usage (from repo root):
#   .\scripts\dev.ps1 setup   # create .venv and install requirements
#   .\scripts\dev.ps1 run     # start the API on http://localhost:8000 (mock LLM by default)
#   .\scripts\dev.ps1 test    # run pytest -q
param([string]$Command = "run", [int]$Port = 8000)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Py = Join-Path $Root ".venv\Scripts\python.exe"

function Ensure-Venv {
    if (-not (Test-Path $Py)) {
        $launcher = Get-Command py -ErrorAction SilentlyContinue
        if ($launcher) {
            # Prefer 3.11 (project target), fall back to any 3.10+ the launcher knows.
            foreach ($v in @("3.11", "3.12", "3.13", "3.10")) {
                & py "-$v" -c "import sys" 2>$null
                if ($LASTEXITCODE -eq 0) { & py "-$v" -m venv .venv; break }
            }
        } else {
            python -m venv .venv
        }
    }
    if (-not (Test-Path $Py)) { throw "Could not create .venv - install Python 3.11+" }
}

switch ($Command) {
    "setup" {
        Ensure-Venv
        & $Py -m pip install --upgrade pip
        & $Py -m pip install -r requirements.txt
    }
    "run" {
        Ensure-Venv
        if (-not $env:LLM_PROVIDER) { Write-Host "LLM_PROVIDER not set in shell; backend/.env or default 'mock' applies." }
        & $Py -m uvicorn backend.api.main:app --host 127.0.0.1 --port $Port --reload --reload-dir backend
    }
    "test" {
        Ensure-Venv
        & $Py -m pytest -q
    }
    default { Write-Host "Unknown command '$Command'. Use: setup | run | test" ; exit 1 }
}
