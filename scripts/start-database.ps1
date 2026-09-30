param([int]$Port = 55432)
$ErrorActionPreference = 'Stop'
$databaseRoot = Split-Path -Parent $PSScriptRoot
$databaseCtl = Join-Path $databaseRoot 'work\postgresql-runtime\pgsql\bin\pg_ctl.exe'
$databaseCluster = Join-Path $databaseRoot 'data\postgres-cluster'
if (-not (Test-Path -LiteralPath $databaseCtl) -or -not (Test-Path -LiteralPath (Join-Path $databaseCluster 'PG_VERSION'))) { throw 'Local PostgreSQL runtime or cluster is missing. See docs/maintenance/player-profiles.md.' }
& $databaseCtl status -D $databaseCluster *> $null
if ($LASTEXITCODE -eq 0) { Write-Output 'Local PostgreSQL is already running.'; exit 0 }
$databaseArguments = @('start', '-D', ('"' + $databaseCluster + '"'), '-l', ('"' + (Join-Path $databaseRoot 'work\postgresql.log') + '"'), '-o', ('"-p ' + $Port + ' -h 127.0.0.1"'), '-w')
$databaseProcess = Start-Process -FilePath $databaseCtl -ArgumentList $databaseArguments -WorkingDirectory $databaseRoot -WindowStyle Hidden -PassThru
if (-not $databaseProcess.WaitForExit(60000)) { throw 'PostgreSQL startup timed out. See work/postgresql.log.' }
if ($databaseProcess.ExitCode -ne 0) { throw 'PostgreSQL did not start. See work/postgresql.log.' }
Write-Output "Local PostgreSQL is ready on 127.0.0.1:$Port."
exit 0
