# The TV as a 3rd monitor exists only while Moonlight streams: Sunshine runs this (as SYSTEM) with
# "on" when a stream starts and "off" when it ends (global_prep_cmd in sunshine.conf).
# Installed copy lives in C:\Program Files\Sunshine\config\ (admin-only) since it runs elevated.
#
# Leaving Moonlight only pauses the session (Sunshine keeps Desktop "running" so it can resume), so
# "off" never ran and the screen stayed (27 Oct). "on" therefore also starts a watcher: as soon as
# Moonlight disconnects it turns the screen off and restarts Sunshine, which ends the paused session,
# so the next connect is a fresh start that runs "on" again.
param([Parameter(Mandatory)][ValidateSet('on', 'off', 'watch')][string]$State)

$SunshineLog = 'C:\Program Files\Sunshine\config\sunshine.log'
$GraceSeconds = 2                   # Mark wants it gone as soon as he leaves Moonlight

function Set-Screen($on) {
    $screen = Get-PnpDevice -FriendlyName 'Virtual Display Driver' -ErrorAction SilentlyContinue
    if (-not $screen) { return }
    if ($on) { $screen | Enable-PnpDevice -Confirm:$false } else { $screen | Disable-PnpDevice -Confirm:$false }
}

function Get-LastEvent($after) {
    # The newest connect/disconnect/quit line logged after $after, as @{Time; Kind}
    $lines = Get-Content $SunshineLog -Tail 500 -ErrorAction SilentlyContinue |
        Select-String 'CLIENT CONNECTED|CLIENT DISCONNECTED|Executing Undo Cmd'
    foreach ($l in @($lines)[-1..-(@($lines).Count)]) {
        if ($l.Line -match '^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)') {
            $time = [datetime]::ParseExact($Matches[1], 'yyyy-MM-dd HH:mm:ss', $null)
            if ($time -lt $after) { return $null }
            $kind = if ($l.Line -match 'Undo') { 'quit' } elseif ($l.Line -match 'DISCONNECTED') { 'gone' } else { 'here' }
            return @{ Time = $time; Kind = $kind }
        }
    }
    return $null
}

switch ($State) {
    'on' {
        Set-Screen $true
        Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
            Where-Object { $_.CommandLine -like '*tv-screen.ps1*watch*' } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Process powershell.exe -WindowStyle Hidden -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-File', "`"$PSCommandPath`"", 'watch'
        Start-Sleep -Seconds 3          # let Windows bring the screen back (4K, extended) before Sunshine captures it
    }
    'off' { Set-Screen $false }
    'watch' {
        $started = (Get-Date).AddSeconds(-2)
        while ($true) {
            Start-Sleep -Seconds 1
            $last = Get-LastEvent $started
            if (-not $last -or $last.Kind -eq 'here') { continue }
            if ($last.Kind -eq 'quit') { exit }                 # quit from Moonlight: "off" has run
            if (((Get-Date) - $last.Time).TotalSeconds -ge $GraceSeconds) {
                Set-Screen $false
                Restart-Service SunshineService                 # ends the paused session
                exit
            }
        }
    }
}
