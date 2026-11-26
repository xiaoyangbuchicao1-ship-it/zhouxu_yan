param(
    [string] $Destination = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# ASCII-only: this file must parse in Windows PowerShell 5.1 on code page 936.
$StartRoot = (Resolve-Path $PSScriptRoot).Path
$ProjectRoot = (Resolve-Path (Join-Path $StartRoot "..\..")).Path
$ComposeFile = Join-Path $ProjectRoot "deploy\app\compose.yaml"
$ComposeEnv = Join-Path $ProjectRoot "deploy\app\.env"

if (-not (Test-Path -LiteralPath $ComposeEnv)) {
    throw "Missing env file: $ComposeEnv"
}

$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
if (-not $Destination) {
    $Destination = Join-Path $ProjectRoot ("backups\zhouxu-migration-" + $Stamp)
}
if (Test-Path -LiteralPath $Destination) {
    throw "Destination already exists: $Destination. Choose an empty or new directory."
}
New-Item -ItemType Directory -Path $Destination -Force | Out-Null
$ResolvedDest = (Resolve-Path $Destination).Path

Write-Host "[1/4] Stopping zhouxu-api cleanly to flush SQLite WAL..."
& docker compose --env-file $ComposeEnv -f $ComposeFile stop zhouxu-api
if ($LASTEXITCODE -ne 0) { throw "Failed to stop zhouxu-api" }

$ApiWasStopped = $true
try {
    Write-Host "[2/4] Exporting Linux runtime-state volume (runs + skills + telemetry)..."
    $RuntimeExport = Join-Path $ResolvedDest "runtime-volume"
    New-Item -ItemType Directory -Path $RuntimeExport -Force | Out-Null
    & docker compose --env-file $ComposeEnv -f $ComposeFile run --rm --no-deps --entrypoint python `
        -v "${RuntimeExport}:/app/export-target" zhouxu-api `
        /app/start/common/runtime_volume.py export --destination /app/export-target
    if ($LASTEXITCODE -ne 0) { throw "runtime_volume export failed" }

    Write-Host "[3/4] Backing up relational DB (zhouxu.db) via SQLite Backup API..."
    $RelExport = Join-Path $ResolvedDest "relational"
    New-Item -ItemType Directory -Path $RelExport -Force | Out-Null
    $BackupPy = "import sqlite3, pathlib; src=sqlite3.connect('/app/data/relational/zhouxu.db'); dst=sqlite3.connect('/app/rel-out/zhouxu.db'); src.backup(dst); dst.execute('PRAGMA journal_mode=DELETE'); ok=dst.execute('PRAGMA integrity_check').fetchone()[0]; dst.close(); src.close(); assert ok=='ok', ok; print('VERIFIED zhouxu.db')"
    & docker compose --env-file $ComposeEnv -f $ComposeFile run --rm --no-deps --entrypoint python `
        -v "${RelExport}:/app/rel-out" zhouxu-api -c $BackupPy
    if ($LASTEXITCODE -ne 0) { throw "zhouxu.db hot backup failed" }

    Write-Host "[4/4] Copying uploaded originals, deliverables, and deployment .env..."
    $FilesSrc = Join-Path $ProjectRoot "data\files"
    if (Test-Path -LiteralPath $FilesSrc) {
        $FilesDst = Join-Path $ResolvedDest "files"
        New-Item -ItemType Directory -Path $FilesDst -Force | Out-Null
        foreach ($Sub in @("originals", "generated", "derived")) {
            $SubSrc = Join-Path $FilesSrc $Sub
            if (Test-Path -LiteralPath $SubSrc) {
                Copy-Item -LiteralPath $SubSrc -Destination (Join-Path $FilesDst $Sub) -Recurse -Force
            }
        }
    }
    Copy-Item -LiteralPath $ComposeEnv -Destination (Join-Path $ResolvedDest ".env") -Force
}
finally {
    if ($ApiWasStopped) {
        Write-Host "Starting zhouxu-api back up..."
        & docker compose --env-file $ComposeEnv -f $ComposeFile up -d --no-deps --wait zhouxu-api
    }
}

Write-Host ""
Write-Host "Migration export complete: $ResolvedDest"
Write-Host "Copy the project folder + this migration folder to the target PC and run backup-restore.cmd."
