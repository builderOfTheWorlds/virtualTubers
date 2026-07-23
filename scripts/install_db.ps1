<#
.SYNOPSIS
    Runs docs/sql/01_create_role_and_database.sql and 02_create_tables.sql
    against the shared Postgres instance, in order. See docs/sql/README.md.

.DESCRIPTION
    Step 1 connects as a superuser (default: postgres) to create the
    "virtualtubers" login role and database.
    Step 2 connects as the new "virtualtubers" role to create its tables.

    Host/port/role-password default to POSTGRES_HOST/POSTGRES_PORT/
    POSTGRES_PASSWORD in .env if present. The superuser password has no
    .env equivalent (it's the shared instance's admin credential, not this
    project's) and is always prompted for.

.EXAMPLE
    .\scripts\install_db.ps1
    (prompts for the postgres superuser password; reuses .env for the rest)

.EXAMPLE
    .\scripts\install_db.ps1 -PgHost 192.168.2.158 -PgPort 5432 -SuperuserName postgres
#>
param(
    [string]$PgHost,
    [int]$PgPort,
    [string]$SuperuserName = "mafober",
    [System.Security.SecureString]$SuperuserPassword,
    [System.Security.SecureString]$RolePassword
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

$RoleName = "virtualtubers"
$DatabaseName = "virtualtubers"
$Sql01Path = Join-Path $repoRoot "docs\sql\01_create_role_and_database.sql"
$Sql02Path = Join-Path $repoRoot "docs\sql\02_create_tables.sql"

$LogDir = Join-Path $repoRoot ".logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$LogFile = Join-Path $LogDir "install_db_log_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"
Start-Transcript -Path $LogFile | Out-Null

function Log($msg) { Write-Host "[install_db] $msg" }

# --- Load .env (host/port/role-password fall back to it if not passed) ------
$envVars = @{}
$envFile = Join-Path $repoRoot ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([A-Z_]+)\s*=\s*(.*)\s*$') {
            $envVars[$matches[1]] = $matches[2]
        }
    }
}
if (-not $PgHost -and $envVars.ContainsKey("POSTGRES_HOST")) { $PgHost = $envVars["POSTGRES_HOST"] }
if (-not $PgPort -and $envVars.ContainsKey("POSTGRES_PORT")) { $PgPort = [int]$envVars["POSTGRES_PORT"] }
if (-not $PgHost) { $PgHost = Read-Host "Postgres host" }
if (-not $PgPort) { $PgPort = Read-Host "Postgres port" }

try {
    # --- Verify psql is available -----------------------------------------------
    if (-not (Get-Command psql -ErrorAction SilentlyContinue)) {
        Write-Error "psql not found on PATH. Install the PostgreSQL client tools and add their bin/ directory to PATH."
        exit 1
    }

    # --- Collect secrets ----------------------------------------------------------
    if (-not $SuperuserPassword) {
        $SuperuserPassword = Read-Host "Password for '$SuperuserName' superuser" -AsSecureString
    }
    if (-not $RolePassword -and $envVars.ContainsKey("POSTGRES_PASSWORD") -and $envVars["POSTGRES_PASSWORD"]) {
        Log "Reusing POSTGRES_PASSWORD from .env for the '$RoleName' role."
        $RolePassword = ConvertTo-SecureString $envVars["POSTGRES_PASSWORD"] -AsPlainText -Force
    }
    if (-not $RolePassword) {
        $RolePassword = Read-Host "New password to set for the '$RoleName' role" -AsSecureString
    }

    function ConvertTo-PlainText([System.Security.SecureString]$secure) {
        [System.Net.NetworkCredential]::new('', $secure).Password
    }

    $superuserPlain = ConvertTo-PlainText $SuperuserPassword
    $rolePlain = ConvertTo-PlainText $RolePassword

    $Step1Args = @("-h", $PgHost, "-p", $PgPort, "-U", $SuperuserName, "-v", "pg_password=$rolePlain", "-f", $Sql01Path)
    $Step2Args = @("-h", $PgHost, "-p", $PgPort, "-U", $RoleName, "-d", $DatabaseName, "-f", $Sql02Path)

    try {
        # --- Step 1: create role + database, as the superuser ----------------------
        Log "Running 01_create_role_and_database.sql as '$SuperuserName' on ${PgHost}:${PgPort}"
        $env:PGPASSWORD = $superuserPlain
        & psql @Step1Args
        if ($LASTEXITCODE -ne 0) {
            Write-Error "01_create_role_and_database.sql failed (exit $LASTEXITCODE)."
            exit 1
        }

        # --- Step 2: create tables, as the new role ---------------------------------
        Log "Running 02_create_tables.sql as '$RoleName' on ${PgHost}:${PgPort}"
        $env:PGPASSWORD = $rolePlain
        & psql @Step2Args
        if ($LASTEXITCODE -ne 0) {
            Write-Error "02_create_tables.sql failed (exit $LASTEXITCODE)."
            exit 1
        }
    }
    finally {
        Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
        $superuserPlain = $null
        $rolePlain = $null
    }

    Log "SUCCESS: database install complete."
    Write-Host "  POSTGRES_HOST=$PgHost"
    Write-Host "  POSTGRES_PORT=$PgPort"
    Write-Host "  POSTGRES_DB=$DatabaseName"
    Write-Host "  POSTGRES_USER=$RoleName"
    Write-Host "  POSTGRES_PASSWORD=<the role password you just entered>"
    Log "Log written to $LogFile"
}
finally {
    Stop-Transcript | Out-Null
}
