# Switch goosebase.org traffic from the NUC to this dev box.
#
# Prerequisites (one-time):
#   1. Generate a key on the dev box if you don't have one:
#        ssh-keygen -t ed25519
#   2. Add the public key to the NUC:
#        type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh root@db.internal.example "cat >> ~/.ssh/authorized_keys"
#
# Usage:  .\scripts\dev-on.ps1
#         .\scripts\dev-on.ps1 -NUC root@db.internal.example   # explicit host

param(
    [string]$NUC = "root@db.internal.example"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

Write-Host "==> Building and starting dev containers..."
Push-Location $RepoRoot
docker compose up -d --build backend frontend
Pop-Location

Write-Host "==> Starting cloudflared on dev box..."
Start-Service cloudflared

Write-Host "==> Stopping cloudflared on NUC ($NUC)..."
ssh $NUC "systemctl stop cloudflared"

Write-Host ""
Write-Host "Dev box is now serving goosebase.org"
Write-Host "Run .\scripts\dev-off.ps1 to hand traffic back to the NUC."
