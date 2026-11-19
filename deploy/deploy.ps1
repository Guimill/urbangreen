# From the PC: test, push to GitHub, then update the server.
#   .\deploy\deploy.ps1
# Exit codes are checked by hand: Windows PowerShell 5.1 turns any stderr output of a native
# program (Django's test runner, git) into an error when $ErrorActionPreference is "Stop".
$ErrorActionPreference = "Continue"
Set-Location (Join-Path $PSScriptRoot "..")
& .\.venv\Scripts\python.exe manage.py test monitor 2>&1 | ForEach-Object { "$_" }
if ($LASTEXITCODE -ne 0) { Write-Host "Tests failed: nothing deployed" -ForegroundColor Red; exit 1 }
git push origin main 2>&1 | ForEach-Object { "$_" }
if ($LASTEXITCODE -ne 0) { Write-Host "git push failed" -ForegroundColor Red; exit 1 }
ssh -i "$HOME\.ssh\cv_deploy_ed25519" root@194.164.199.244 "/srv/urbangreen/deploy/update.sh"
