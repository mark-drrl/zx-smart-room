# Ships a change to the room: runs the tests, and only if they pass restarts Zephyr (and, with
# -HomeScreen, rebuilds and installs the TV's Home screen). Nothing is restarted if a test fails.
#   powershell -ExecutionPolicy Bypass -File .\deploy.ps1          # Zephyr only
#   powershell -ExecutionPolicy Bypass -File .\deploy.ps1 -HomeScreen    # Zephyr and the Home screen
param([switch]$HomeScreen)
$ErrorActionPreference = 'Stop'
$py  = 'C:\Users\markd\AppData\Local\Programs\Python\Python312\python.exe'
$log = Join-Path $PSScriptRoot 'voice\assistant.log'

Write-Host '1/3  Tests'
& $py -m pytest (Join-Path $PSScriptRoot 'tests') -q -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { Write-Host 'Tests failed: nothing was deployed.' -ForegroundColor Red; exit 1 }

if ($HomeScreen) {
    Write-Host '2/3  Home screen'
    & (Join-Path $PSScriptRoot 'deploy-home.ps1')
} else {
    Write-Host '2/3  Home screen: unchanged (use -HomeScreen to redeploy it)'
}

Write-Host '3/3  Restarting Zephyr'
schtasks /End /TN 'Zephyr' 2>$null | Out-Null
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -like '*assistant.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -Confirm:$false }
$started = Get-Date
schtasks /Run /TN 'Zephyr' | Out-Null
$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    if ((Get-Item $log).LastWriteTime -gt $started) {
        $tail = Get-Content $log -Tail 3
        if ($tail -match 'crashed') { Write-Host "Zephyr failed to start:`n$($tail -join "`n")" -ForegroundColor Red; exit 1 }
        if ($tail -match 'replies play') { Write-Host 'Zephyr is listening. Reconnect the phone mic (AndroidMic > Connect).' -ForegroundColor Green; exit 0 }
    }
}
Write-Host 'Zephyr did not report in within 90 s: check voice\assistant.log' -ForegroundColor Yellow
exit 1
