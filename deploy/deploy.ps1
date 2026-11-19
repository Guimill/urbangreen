# From the PC: test, push to GitHub, then update the server.
#   .\deploy\deploy.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
& .\.venv\Scripts\python.exe manage.py test monitor
if ($LASTEXITCODE -ne 0) { throw "Tests failed: nothing deployed" }
git push origin main
if ($LASTEXITCODE -ne 0) { throw "git push failed" }
ssh -i "$HOME\.ssh\cv_deploy_ed25519" root@194.164.199.244 "/srv/urbangreen/deploy/update.sh"
