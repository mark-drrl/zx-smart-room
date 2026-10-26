# The TV as a 3rd monitor exists only while Moonlight streams: Sunshine runs this (as SYSTEM) with
# "on" when a stream starts and "off" when it ends (global_prep_cmd in sunshine.conf).
# Installed copy lives in C:\Program Files\Sunshine\config\ (admin-only) since it runs elevated.
param([Parameter(Mandatory)][ValidateSet('on', 'off')][string]$State)

$screen = Get-PnpDevice -FriendlyName 'Virtual Display Driver' -ErrorAction SilentlyContinue
if (-not $screen) { exit 0 }
if ($State -eq 'on') {
    $screen | Enable-PnpDevice -Confirm:$false
    Start-Sleep -Seconds 3          # let Windows bring the screen back (4K, extended) before Sunshine captures it
} else {
    $screen | Disable-PnpDevice -Confirm:$false
}
