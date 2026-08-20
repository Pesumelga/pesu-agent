# PowerShell script for Slack Agent Mode Launcher
param (
    [int]$Port = 9222,
    [switch]$Status,
    [switch]$Restart
)

$slackExe = (Get-ChildItem -Path "$env:LOCALAPPDATA\slack\app-*\slack.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1).FullName
if (-not $slackExe) {
    $slackExe = "$env:LOCALAPPDATA\slack\slack.exe"
}

# 1. Check CDP endpoint
$cdpReady = $false
try {
    $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/json" -TimeoutSec 1 -UseBasicParsing -ErrorAction Stop
    if ($resp.StatusCode -eq 200) {
        $cdpReady = $true
    }
} catch {}

$slackProcs = Get-Process -Name "slack" -ErrorAction SilentlyContinue

if ($Status) {
    if ($cdpReady) {
        Write-Host "Slack Agent Mode" -ForegroundColor Cyan
        Write-Host "● Ready" -ForegroundColor Green
        Write-Host "Slack is running with CDP active on port $Port."
        exit 0
    } elseif ($slackProcs) {
        Write-Host "Slack Agent Mode" -ForegroundColor Cyan
        Write-Host "● Restart Required" -ForegroundColor Yellow
        Write-Host "Agent 백그라운드 모드를 사용하려면 Slack을 한 번 재시작해야 합니다."
        exit 2
    } else {
        Write-Host "Slack Agent Mode" -ForegroundColor Cyan
        Write-Host "● Off" -ForegroundColor DarkGray
        Write-Host "Slack is not running."
        exit 1
    }
}

if ($cdpReady) {
    Write-Host "[SUCCESS] Slack CDP is already active on port $Port! Reusing running instance." -ForegroundColor Green
    Write-Host "Slack Agent Mode" -ForegroundColor Cyan
    Write-Host "● Ready" -ForegroundColor Green
    exit 0
}

if ($slackProcs -and -not $Restart) {
    Write-Host "Slack Agent Mode" -ForegroundColor Cyan
    Write-Host "● Restart Required" -ForegroundColor Yellow
    Write-Host "[WARNING] Slack이 일반 모드로 실행 중입니다." -ForegroundColor Yellow
    Write-Host "사용자의 작업 보호를 위해 실행 중인 Slack을 자동으로 종료하지 않습니다."
    Write-Host "재시작하려면 다음 명령을 실행하세요: .\scripts\start-agent-slack.ps1 -Restart" -ForegroundColor Cyan
    exit 2
}

if ($slackProcs -and $Restart) {
    Write-Host "[INFO] Gracefully terminating running Slack processes..."
    $slackProcs | Stop-Process -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

Write-Host "[INFO] Starting Slack with --remote-debugging-port=$Port..."
Start-Process -FilePath $slackExe -ArgumentList "--remote-debugging-port=$Port"

Write-Host "[INFO] Waiting for CDP endpoint at http://127.0.0.1:$Port/json..."
$ready = $false
for ($i = 0; $i -lt 25; $i++) {
    Start-Sleep -Seconds 1
    Write-Host -NoNewline "."
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/json" -TimeoutSec 1 -UseBasicParsing -ErrorAction Stop
        if ($resp.StatusCode -eq 200) {
            $ready = $true
            break
        }
    } catch {}
}

Write-Host ""
if ($ready) {
    Write-Host "[SUCCESS] Slack Agent Mode is now Ready on port $Port!" -ForegroundColor Green
    Write-Host "Slack Agent Mode" -ForegroundColor Cyan
    Write-Host "● Ready" -ForegroundColor Green
    exit 0
} else {
    Write-Host "[ERROR] Failed to start Slack with CDP on port $Port." -ForegroundColor Red
    exit 1
}
