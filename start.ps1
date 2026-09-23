param([int]$Port = 8765, [string]$BindAddress = "127.0.0.1", [switch]$Lan)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Please create .venv and install requirements.txt first. See README.md."
}
if ($Lan) { $BindAddress = "0.0.0.0" }
$arguments = @("-m", "uvicorn", "app:app", "--host", $BindAddress, "--port", "$Port")
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot ".env")) { $arguments += @("--env-file", ".env") }
Write-Host "GAMEPLAN: http://127.0.0.1:$Port"
if ($Lan) { Write-Host "Trusted LAN only. Do not expose this development server to the public Internet." }
& $python @arguments
