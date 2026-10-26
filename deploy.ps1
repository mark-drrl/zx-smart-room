# Ships a change to the room, the way a release pipeline would:
#   1. checks   lint (ruff) + tests with a coverage floor. Any failure: nothing is deployed.
#   2. clean    only committed code is deployed (so every deploy can be traced and rolled back).
#   3. Home     with -HomeScreen, rebuilds and installs the TV's Home screen too.
#   4. restart  restarts Zephyr through her scheduled task.
#   5. health   voice\health.py: Zephyr up and stable, plus every service she depends on.
#   6. tag      a healthy deploy is tagged deployed-<date>-<time>.
#   7. rollback if Zephyr isn't healthy, the last good deployment's code is restored and restarted.
#
#   powershell -ExecutionPolicy Bypass -File .\deploy.ps1               # Zephyr only
#   powershell -ExecutionPolicy Bypass -File .\deploy.ps1 -HomeScreen   # Zephyr and the Home screen
param([switch]$HomeScreen)
$ErrorActionPreference = 'Stop'
$py   = 'C:\Users\markd\AppData\Local\Programs\Python\Python312\python.exe'
$root = $PSScriptRoot
$log  = Join-Path $root 'voice\assistant.log'
Set-Location $root
$env:PYTHONIOENCODING = 'utf-8'

function Restart-Zephyr {
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
            # only lines written after this restart: earlier "crashed" lines (e.g. from the version being
            # rolled back) must not count against the new one - that misjudged the first rollback drill
            $since = $started.ToString('yyyy-MM-dd HH:mm:ss')
            $new = Get-Content $log -Tail 40 | Where-Object { $_.Length -ge 19 -and $_.Substring(0, 19) -ge $since }
            if ($new -match 'crashed') { return $false }
            if ($new -match 'replies play') { return $true }
        }
    }
    return $false
}

function Test-Health {
    & $py -P (Join-Path $root 'voice\health.py') | ForEach-Object { Write-Host "       $_" }
    return ($LASTEXITCODE -eq 0)
}

Write-Host '1/6  Checks: lint + tests'
& $py -m ruff check voice tests
if ($LASTEXITCODE -ne 0) { Write-Host 'Lint failed: nothing was deployed.' -ForegroundColor Red; exit 1 }
& $py -m pytest -q --cov --cov-fail-under=35 --cov-report=
if ($LASTEXITCODE -ne 0) { Write-Host 'Tests failed: nothing was deployed.' -ForegroundColor Red; exit 1 }

Write-Host '2/6  Only committed code'
if (git status --porcelain) { Write-Host 'Uncommitted changes: commit them first, so this deploy can be traced and rolled back.' -ForegroundColor Red; git status --short; exit 1 }
$commit   = (git rev-parse --short HEAD).Trim()
$lastGood = (git tag --list 'deployed-*' --sort=-creatordate | Select-Object -First 1)

if ($HomeScreen) {
    Write-Host '3/6  Home screen'
    & (Join-Path $root 'deploy-home.ps1')
} else {
    Write-Host '3/6  Home screen: unchanged (use -HomeScreen to redeploy it)'
}

Write-Host "4/6  Restarting the Home keeper and Zephyr on $commit"
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -like '*home-keeper.ps1*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -Confirm:$false }
schtasks /Run /TN 'Frame Home Keeper' | Out-Null
$up = Restart-Zephyr

Write-Host '5/6  Health check'
if ($up -and (Test-Health)) {
    $tag = 'deployed-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
    git tag $tag
    Write-Host "6/6  Healthy. Tagged $tag ($commit)." -ForegroundColor Green
    exit 0
}

Write-Host "6/6  Zephyr is not healthy on ${commit}: rolling back." -ForegroundColor Red
if (-not $lastGood) { Write-Host 'No earlier good deployment to roll back to: check voice\assistant.log.' -ForegroundColor Red; exit 1 }
git checkout $lastGood -- voice
if ((Restart-Zephyr) -and (Test-Health)) {
    Write-Host "Rolled back to ${lastGood}: Zephyr is up on the last good code." -ForegroundColor Yellow
    Write-Host "The working folder now holds $lastGood's voice\ files; fix the problem on $commit, then 'git checkout HEAD -- voice' and deploy again." -ForegroundColor Yellow
} else {
    Write-Host "Rollback to $lastGood didn't come up healthy either: check voice\assistant.log." -ForegroundColor Red
}
exit 1
