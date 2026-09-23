param([ValidateRange(1024,65535)][int]$Port = 8767, [switch]$NoWindow, [switch]$NoAutoStart, [switch]$Workbench)
$ErrorActionPreference = 'Stop'
$monitorRoot = $PSScriptRoot
$monitorPython = Join-Path $monitorRoot '.venv\Scripts\python.exe'
$monitorBase = "http://127.0.0.1:$Port"
$monitorUrl = "$monitorBase/monitor"
if ($NoAutoStart) { $monitorUrl += '?monitor=off' }
$launchMutex = New-Object System.Threading.Mutex($false, "Local\GameplanDesktopMonitor$Port")
$ownsMutex = $false
function Get-MonitorService {
    try { return Invoke-RestMethod -Uri "$monitorBase/api/monitor/status" -TimeoutSec 2 -UseBasicParsing }
    catch { return $null }
}
function Test-MonitorPort {
    $clientSocket = New-Object System.Net.Sockets.TcpClient
    try { return $clientSocket.ConnectAsync('127.0.0.1', $Port).Wait(500) -and $clientSocket.Connected }
    catch { return $false }
    finally { $clientSocket.Dispose() }
}
try {
    $ownsMutex = $launchMutex.WaitOne(15000)
    if (-not $ownsMutex) { throw 'Another monitor launcher is still starting. Please try again shortly.' }
    $monitorService = Get-MonitorService
    if (-not $monitorService -or $monitorService.application -ne 'gameplan-desktop-monitor') {
        if (Test-MonitorPort) { throw "Port $Port belongs to another service or an older workbench. Use another port or restart the updated workbench." }
        if (-not (Test-Path -LiteralPath $monitorPython)) { throw 'Project .venv is missing. Install requirements.txt first.' }
        $logDirectory = Join-Path $monitorRoot 'work\monitor-logs'
        New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
        $logName = "monitor-$Port-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        $serverArguments = "-m uvicorn monitor_app:app --host 127.0.0.1 --port $Port"
        $envFile = Join-Path $monitorRoot '.env'
        if (Test-Path -LiteralPath $envFile) { $serverArguments += (' --env-file "{0}"' -f $envFile) }
        $diagnosticsConfigured = (Test-Path -LiteralPath $envFile) -and (Select-String -LiteralPath $envFile -Pattern '^\s*(export\s+)?GAMEPLAN_SKILL_DIAGNOSTICS\s*=' -Quiet)
        if (-not (Test-Path Env:GAMEPLAN_SKILL_DIAGNOSTICS) -and -not $diagnosticsConfigured) { $env:GAMEPLAN_SKILL_DIAGNOSTICS = '1' }
        $monitorProcess = Start-Process -FilePath $monitorPython -ArgumentList $serverArguments -WorkingDirectory $monitorRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory "$logName.out.log") -RedirectStandardError (Join-Path $logDirectory "$logName.err.log") -PassThru
        $ready = $false
        for ($attempt = 0; $attempt -lt 40; $attempt++) {
            Start-Sleep -Milliseconds 250
            $monitorService = Get-MonitorService
            if ($monitorService -and $monitorService.application -eq 'gameplan-desktop-monitor') { $ready = $true; break }
            if ($monitorProcess.HasExited) { break }
        }
        if (-not $ready) { throw "Monitor failed to start. Check logs in $logDirectory." }
    }
    if ($NoWindow) { Write-Output $monitorUrl; exit 0 }
    if (-not $Workbench) {
        $panelArguments = "-m gameplan.monitoring.overlay --port $Port"
        if ($NoAutoStart) { $panelArguments += ' --no-auto' }
        $panelLogDirectory = Join-Path $monitorRoot 'work\monitor-logs'
        New-Item -ItemType Directory -Path $panelLogDirectory -Force | Out-Null
        $panelLog = 'panel-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
        Start-Process -FilePath $monitorPython -ArgumentList $panelArguments -WorkingDirectory $monitorRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $panelLogDirectory "$panelLog.out.log") -RedirectStandardError (Join-Path $panelLogDirectory "$panelLog.err.log") | Out-Null
        exit 0
    }
    $browserCandidates = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
        (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
    )
    $monitorBrowser = $browserCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if ($monitorBrowser) { Start-Process -FilePath $monitorBrowser -ArgumentList "--app=$monitorUrl", '--new-window' -WindowStyle Normal | Out-Null }
    else { Start-Process $monitorUrl | Out-Null }
} catch {
    if ($NoWindow) { Write-Error $_; exit 1 }
    $popup = New-Object -ComObject WScript.Shell
    $popup.Popup($_.Exception.Message, 0, 'GAMEPLAN Monitor', 16) | Out-Null
    exit 1
} finally {
    if ($ownsMutex) { $launchMutex.ReleaseMutex() }
    $launchMutex.Dispose()
}
