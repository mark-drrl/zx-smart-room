# Builds, signs and installs the Home launcher on The Frame, then opens it.
# Run from PowerShell:  powershell -ExecutionPolicy Bypass -File ".\deploy-home.ps1"
$ErrorActionPreference = 'Stop'
$tv      = '192.168.1.97'
$device  = 'QA50LS03AAUXZN'
$profile = 'FrameHome'
$src     = Join-Path $PSScriptRoot 'frame-launcher'
$build   = Join-Path $env:TEMP 'frame-home-build'
$sdb     = 'C:\tizen-studio\tools\sdb.exe'
$tizen   = 'C:\tizen-studio\tools\ide\bin\tizen.bat'

Remove-Item $build -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $build | Out-Null
foreach ($i in 'config.xml', 'index.html', 'icon.png', 'css', 'js', 'fonts', 'icons', 'media') {
    Copy-Item (Join-Path $src $i) (Join-Path $build $i) -Recurse
}

& $sdb connect $tv | Out-Null
& $tizen package -t wgt -s $profile -- $build | Out-Null
$wgt = Join-Path $build 'Home.wgt'
if (-not (Test-Path $wgt)) { throw 'Packaging failed: no Home.wgt was produced. Check the FrameHome certificate profile.' }
Copy-Item $wgt (Join-Path $PSScriptRoot 'Home.wgt') -Force

& $tizen install -n $wgt -t $device | Select-String 'install completed|successfully installed|error|fail'
& $tizen run -p FrameHome1.Home -t $device | Select-String 'successfully launched|error|fail'
