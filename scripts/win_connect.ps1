#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Connect your Windows 11 machine to radioman over USB.

.DESCRIPTION
    Detects the USB gadget adapter, assigns a static IP (10.55.0.2),
    removes any default-route pollution, and verifies the Pi is reachable.
    Optionally enables Windows Internet Connection Sharing (ICS) so the Pi
    can reach the internet through your machine.

.PARAMETER Share
    Enable ICS so the Pi can route internet traffic through this machine.
    Note: ICS changes the USB adapter to 192.168.137.1 (its subnet).
    This script re-adds 10.55.0.2 so SSH still works, but the Pi itself
    must have its usb0 interface set to DHCP to actually use internet.
    See: README.md — "Internet sharing (Windows)"

.PARAMETER PiIP
    IP address of the Pi on the USB gadget. Default: 10.55.0.1

.PARAMETER WinIP
    IP to assign to this machine's USB adapter. Default: 10.55.0.2

.EXAMPLE
    # Run from repo root — SSH-ready only
    powershell -ExecutionPolicy Bypass -File scripts\win_connect.ps1

.EXAMPLE
    # SSH + internet sharing
    powershell -ExecutionPolicy Bypass -File scripts\win_connect.ps1 -Share
#>

param(
    [switch]$Share,
    [string]$PiIP  = '10.55.0.1',
    [string]$WinIP = '10.55.0.2'
)

$PREFIX = 24
$ErrorActionPreference = 'Stop'

# ── Colour helpers ─────────────────────────────────────────────────────────────
function log  { param($m) Write-Host "[radioman] $m" -ForegroundColor Green  }
function warn { param($m) Write-Host "[warning]  $m" -ForegroundColor Yellow }
function err  { param($m) Write-Host "[error]    $m" -ForegroundColor Red; exit 1 }
function info { param($m) Write-Host "[info]     $m" -ForegroundColor Cyan  }

log "Starting win_connect..."

# ── Detect USB gadget adapter ──────────────────────────────────────────────────
# The Pi USB gadget (g_ether) appears on Windows as an RNDIS-class device.
# Common description strings: "Remote NDIS Compatible Device",
# "USB Ethernet/RNDIS Gadget", "RNDIS/Ethernet Gadget"
$usbAdapter = Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object {
    $_.Status -ne 'Disabled' -and (
        $_.InterfaceDescription -match 'RNDIS'          -or
        $_.InterfaceDescription -match 'USB Ethernet'   -or
        $_.InterfaceDescription -match 'Gadget'         -or
        $_.Name                 -match 'USB'
    )
} | Sort-Object -Property { $_.Status -eq 'Up' } -Descending |
    Select-Object -First 1

if (-not $usbAdapter) {
    err @"
No USB gadget adapter found.
  - Make sure the Pi is booted and the USB data cable is plugged into
    the Pi's USB port (not the PWR port).
  - Look in Settings > Network & Internet > Advanced network settings
    for a new adapter after plugging in.
  - On first connection Windows may need a moment to install the RNDIS driver.
"@
}

log "Found USB adapter: $($usbAdapter.Name)  ($($usbAdapter.InterfaceDescription))"

# ── Assign static IP ───────────────────────────────────────────────────────────
$existing = Get-NetIPAddress -InterfaceIndex $usbAdapter.ifIndex `
    -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -eq $WinIP }

if ($existing) {
    info "USB adapter already has $WinIP — skipping assignment."
} else {
    # Remove all existing IPv4 addresses and routes on this adapter first
    Get-NetIPAddress -InterfaceIndex $usbAdapter.ifIndex -AddressFamily IPv4 `
        -ErrorAction SilentlyContinue |
        Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
    Get-NetRoute -InterfaceIndex $usbAdapter.ifIndex -ErrorAction SilentlyContinue |
        Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue

    log "Assigning $WinIP/$PREFIX to $($usbAdapter.Name) ..."
    New-NetIPAddress `
        -InterfaceIndex $usbAdapter.ifIndex `
        -IPAddress      $WinIP `
        -PrefixLength   $PREFIX `
        -ErrorAction    Stop | Out-Null
    log "USB adapter IP set to $WinIP"
}

# Remove any default route the USB adapter may have added — prevents it from
# hijacking your machine's internet traffic.
Get-NetRoute -InterfaceIndex $usbAdapter.ifIndex -DestinationPrefix '0.0.0.0/0' `
    -ErrorAction SilentlyContinue |
    Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue

# ── Verify Pi is reachable ─────────────────────────────────────────────────────
log "Pinging Pi at $PiIP ..."
if (Test-Connection -ComputerName $PiIP -Count 2 -Quiet -TimeoutSeconds 3) {
    log "Pi is reachable at $PiIP"
} else {
    warn "Pi is not responding to ping yet."
    warn "  - It may still be booting."
    warn "  - Check that usb0 is configured on the Pi: ip addr show usb0"
    warn "  - Verify the Pi-side NM profile: nmcli connection show usb-gadget"
}

# ── Internet sharing (optional) ────────────────────────────────────────────────
if ($Share) {
    Write-Host ""
    log "Enabling internet connection sharing..."

    # Find the upstream adapter (holds the default route to internet)
    $defaultRoute = Get-NetRoute -DestinationPrefix '0.0.0.0/0' |
        Sort-Object RouteMetric | Select-Object -First 1
    $upstreamAdapter = if ($defaultRoute) {
        Get-NetAdapter -InterfaceIndex $defaultRoute.ifIndex -ErrorAction SilentlyContinue
    }

    if (-not $upstreamAdapter) {
        warn "Could not detect the upstream internet adapter."
        warn "Enable ICS manually:"
        warn "  Control Panel > Network Connections"
        warn "  Right-click your WiFi > Properties > Sharing tab"
        warn "  Check 'Allow other network users to connect...'"
        warn "  Select '$($usbAdapter.Name)' as the home network connection."
    } else {
        info "Upstream adapter: $($upstreamAdapter.Name)"

        try {
            $mgr = New-Object -ComObject HNetCfg.HNetShare.1

            # Disable any currently shared connections first
            $mgr.EnumEveryConnection | ForEach-Object {
                $cfg = $mgr.INetSharingConfigurationForINetConnection($_)
                if ($cfg.SharingEnabled) { $cfg.DisableSharing() }
            }

            # Enable public sharing on the upstream (internet) adapter
            $upConn = $mgr.EnumEveryConnection |
                Where-Object { ($mgr.NetConnectionProps($_)).Name -eq $upstreamAdapter.Name } |
                Select-Object -First 1

            # Enable private sharing on the USB (downstream) adapter
            $usbConn = $mgr.EnumEveryConnection |
                Where-Object { ($mgr.NetConnectionProps($_)).Name -eq $usbAdapter.Name } |
                Select-Object -First 1

            if ($upConn -and $usbConn) {
                ($mgr.INetSharingConfigurationForINetConnection($upConn)).EnableSharing(0)   # 0 = public
                ($mgr.INetSharingConfigurationForINetConnection($usbConn)).EnableSharing(1)  # 1 = private
                log "ICS enabled: $($upstreamAdapter.Name) → $($usbAdapter.Name)"
            } else {
                warn "Could not find both adapters in ICS connection list."
                warn "Enable ICS manually via Control Panel > Network Connections."
            }
        } catch {
            warn "ICS automation failed: $_"
            warn "Enable ICS manually:"
            warn "  Control Panel > Network Connections"
            warn "  Right-click $($upstreamAdapter.Name) > Properties > Sharing"
        }

        # ── Re-add static IP after ICS ─────────────────────────────────────────
        # ICS changes the USB adapter to 192.168.137.1/24.
        # Re-add 10.55.0.2 as a secondary address so SSH stays working.
        Start-Sleep -Seconds 2
        $alreadySet = Get-NetIPAddress -InterfaceIndex $usbAdapter.ifIndex `
            -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -eq $WinIP }

        if (-not $alreadySet) {
            try {
                New-NetIPAddress `
                    -InterfaceIndex $usbAdapter.ifIndex `
                    -IPAddress      $WinIP `
                    -PrefixLength   $PREFIX `
                    -ErrorAction    Stop | Out-Null
                info "Re-added $WinIP so SSH to $PiIP still works."
            } catch {
                warn "Could not re-add $WinIP after ICS: $_"
                warn "SSH may not work until you manually set $WinIP on the USB adapter."
            }
        }

        Write-Host ""
        warn "ICS NOTE: The Pi must have usb0 in DHCP mode to actually use internet."
        warn "  On the Pi, run:"
        warn "    sudo nmcli connection modify usb-gadget ipv4.method auto"
        warn "    sudo nmcli connection up usb-gadget"
        warn "  The Pi will get a 192.168.137.x address from Windows ICS."
        warn "  SSH will still work at $PiIP (static NM profile re-enabled)."
        warn "  To restore static-only mode on the Pi:"
        warn "    sudo nmcli connection modify usb-gadget ipv4.method manual ipv4.addresses $PiIP/$PREFIX"
        warn "    sudo nmcli connection up usb-gadget"
    }
}

# ── Done ───────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor DarkGray
Write-Host " SSH:        ssh kali@$PiIP"                      -ForegroundColor White
Write-Host " Dashboard:  http://${PiIP}:8080"                 -ForegroundColor White
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor DarkGray
Write-Host ""
