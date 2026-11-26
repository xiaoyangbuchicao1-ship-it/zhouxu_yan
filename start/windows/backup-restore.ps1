param(
    [Parameter(Mandatory = $true)]
    [string] $Source,
    [switch] $Force
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# ASCII-only: this file must parse in Windows PowerShell 5.1 on code page 936.
$StartRoot = (Resolve-Path $PSScriptRoot).Path
$ProjectRoot = (Resolve-Path (Join-Path $StartRoot "..\..")).Path
$ComposeFile = Join-Path $ProjectRoot "deploy\app\compose.yaml"
$ComposeEnv = Join-Path $ProjectRoot "deploy\app\.env"

$ResolvedSrc = (Resolve-Path $Source).Path
$RuntimeSrc = Join-Path $ResolvedSrc "runtime-volume"
$RelSrc = Join-Path $ResolvedSrc "relational\zhouxu.db"

if (-not (Test-Path -LiteralPath $RuntimeSrc)) {
    throw "Missing runtime-volume directory in backup: $RuntimeSrc"
}
if (-not (Test-Path -LiteralPath $RelSrc)) {
    throw "Missing relational/zhouxu.db in backup: $RelSrc"
}
if (-not (Test-Path -LiteralPath $ComposeEnv)) {
    $EnvSrc = Join-Path $ResolvedSrc ".env"
    if (Test-Path -LiteralPath $EnvSrc) {
        Copy-Item -LiteralPath $EnvSrc -Destination $ComposeEnv -Force
        Write-Host "Copied .env from migration bundle."
    } else {
        throw "Missing env file: $ComposeEnv"
    }
}

$ExistingRel = Join-Path $ProjectRoot "data\relational\zhouxu.db"
if ((Test-Path -LiteralPath $ExistingRel) -and (-not $Force)) {
    throw "Existing zhouxu.db found at $ExistingRel. Re-run with -Force only if you intentionally want to replace it."
}

Write-Host "[1/4] Stopping running app services cleanly..."
& docker compose --env-file $ComposeEnv -f $ComposeFile stop zhouxu-web zhouxu-api
if ($LASTEXITCODE -ne 0) { throw "Failed to stop containers" }

Write-Host "[2/4] Restoring relational DB and user files..."
$RelDir = Join-Path $ProjectRoot "data\relational"
New-Item -ItemType Directory -Path $RelDir -Force | Out-Null
foreach ($Sidecar in @("zhouxu.db", "zhouxu.db-wal", "zhouxu.db-shm")) {
    $TargetSidecar = Join-Path $RelDir $Sidecar
    if (Test-Path -LiteralPath $TargetSidecar) {
        Remove-Item -LiteralPath $TargetSidecar -Force
    }
}
Copy-Item -LiteralPath $RelSrc -Destination $ExistingRel -Force

$FilesSrc = Join-Path $ResolvedSrc "files"
if (Test-Path -LiteralPath $FilesSrc) {
    $FilesDst = Join-Path $ProjectRoot "data\files"
    New-Item -ItemType Directory -Path $FilesDst -Force | Out-Null
    foreach ($Sub in @("originals", "generated", "derived")) {
        $SubSrc = Join-Path $FilesSrc $Sub
        if (Test-Path -LiteralPath $SubSrc) {
            Copy-Item -LiteralPath $SubSrc -Destination (Join-Path $FilesDst $Sub) -Recurse -Force
        }
    }
}

Write-Host "[3/4] Restoring Linux runtime-state volume..."
$RestorePy = "import shutil, sqlite3, pathlib; src=pathlib.Path('/app/import-src'); dst=pathlib.Path('/app/backend/.runtime'); dst.mkdir(parents=True, exist_ok=True); existing=[p for p in dst.iterdir() if p.name in ('runs.sqlite3','maritime-office.sqlite3')]; assert not existing or '$($Force.IsPresent)' == 'True', 'runtime_volume_not_empty'; [p.unlink() for p in dst.glob('*') if p.is_file()]; [shutil.copy2(f, dst / f.name) for f in src.iterdir() if f.is_file()]; [sqlite3.connect(dst / f.name).execute('PRAGMA integrity_check').fetchone()[0] == 'ok' for f in src.iterdir() if f.suffix == '.sqlite3']; print('RESTORED runtime-state')"
& docker compose --env-file $ComposeEnv -f $ComposeFile run --rm --no-deps --entrypoint python `
    -v "${RuntimeSrc}:/app/import-src:ro" zhouxu-api -c $RestorePy
if ($LASTEXITCODE -ne 0) { throw "Failed to restore runtime-state volume" }

Write-Host "[4/4] Starting all services..."
& docker compose --env-file $ComposeEnv -f $ComposeFile up -d --wait
if ($LASTEXITCODE -ne 0) { throw "Failed to start restored stack" }

Write-Host "Restore complete and services are healthy."
