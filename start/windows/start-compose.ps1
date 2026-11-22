$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# ASCII-only: this file must parse in Windows PowerShell 5.1 on code page 936.
$StartRoot = (Resolve-Path $PSScriptRoot).Path
$ProjectRoot = (Resolve-Path (Join-Path $StartRoot "..\..")).Path
$ComposeFile = Join-Path $ProjectRoot "deploy\app\compose.yaml"
$ComposeEnv = Join-Path $ProjectRoot "deploy\app\.env"

function Invoke-Compose([string[]] $Arguments) {
    & docker compose --env-file $ComposeEnv -f $ComposeFile @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed with exit code $LASTEXITCODE"
    }
}

function Assert-DockerEngine {
    # Native stderr is data at this external boundary, including in PS 5.1.
    $ErrorActionPreference = "Continue"
    $EngineOutput = (& docker info --format "{{.OSType}}" 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        if ($EngineOutput -match "permission denied|access is denied|access denied|E_ACCESSDENIED") {
            throw "Docker access denied. Run this launcher in a normal terminal under the Docker Desktop user; a restricted sandbox needs explicit Docker access. Do not change the daemon or pipe permissions."
        }
        throw "Docker engine is unreachable. Start Docker Desktop, wait for the engine to be ready, then retry. Docker reported: $EngineOutput"
    }
    if ($EngineOutput -ne "linux") {
        throw "Docker is using '$EngineOutput' containers. Switch Docker Desktop to Linux containers, then retry."
    }
    & docker compose version *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose plugin is unavailable. Update Docker Desktop or install the Compose plugin, then retry."
    }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "docker is required; install Docker Desktop and start it first"
}
Assert-DockerEngine
if (-not (Test-Path -LiteralPath $ComposeFile)) {
    throw "missing Compose file: $ComposeFile"
}
if (-not (Test-Path -LiteralPath $ComposeEnv)) {
    throw "missing env file: $ComposeEnv; copy .env.example to .env and set secrets"
}

Invoke-Compose @("config", "--quiet")
Invoke-Compose @("up", "-d", "--build")
Invoke-Compose @("ps", "-a")
