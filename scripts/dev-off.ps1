# Hand goosebase.org traffic back to the NUC and tear down dev containers.
# See dev-on.ps1 for SSH key prerequisites.
#
# Usage:  .\scripts\dev-off.ps1
#         .\scripts\dev-off.ps1 -NUC root@db.internal.example   # explicit host

param(
    [string]$NUC = "root@db.internal.example"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

Write-Host "==> Stopping cloudflared on dev box..."
Stop-Service cloudflared

Write-Host "==> Starting cloudflared on NUC ($NUC)..."
ssh $NUC "systemctl start cloudflared"

Write-Host "==> Stopping dev containers..."
Push-Location $RepoRoot
docker compose down
Pop-Location

Write-Host ""
Write-Host "NUC is now serving goosebase.org"
