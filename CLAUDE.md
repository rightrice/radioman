# radioman — Claude context

Everything needed to pick up where we left off.

---

## What this project is

Tamagotchi-style Wi-Fi audit console for the **Raspberry Pi Zero 2W**.
- Passive Wi-Fi scanning via **bettercap** (PMKID + EAPOL handshake capture)
- Auto-crack queue (hashcat + aircrack-ng + rockyou)
- Network graph, LAN host discovery, local AI assistant (IBM Granite 4.0 350M via llama.cpp)
- Waveshare 2.13" e-ink display, PiSugar 2 battery (optional)
- Flask web dashboard, XPLT cloud sync
- All daemon code lives in `daemon/`, setup scripts in `setup/`

---

## Target hardware

| Component | Detail |
|---|---|
| SBC | Raspberry Pi Zero 2W (BCM2710A1, quad-core Cortex-A53, 512MB RAM) |
| Wireless | BCM43430A1 (CYW43438) — monitor mode via nexmon DKMS |
| Display | Waveshare 2.13" e-ink (250×122) — SPI |
| Battery | PiSugar 2 — I2C at 0x75 (optional, gracefully skipped if absent) |
| OS | **Ubuntu Server 24.04 LTS arm64** |

---

## OS decision — Ubuntu Server, not Kali

We switched from Kali Linux to **Ubuntu Server 24.04 LTS** because:
- Kali was unstable on 512MB RAM (OOM during `apt upgrade`, dropped SSH sessions, network hangs)
- Kali ships a full desktop (kali-desktop-xfce) even on headless images — heavy even after removal
- Kali's rolling release is fragile on embedded hardware
- Ubuntu Server 24.04 LTS is headless by default, lighter, stable LTS base

**Default user on Ubuntu Pi is `ubuntu`, not `kali`.** All scripts use `$SUDO_USER` to detect the real username — post-install instructions will say `ubuntu@radioman.local` automatically.

---

## Current Pi status (as of last session)

- Ubuntu Server 24.04 LTS arm64 flashed and booted
- WiFi connected and working (NetworkManager, static IP pre-configured via NM profile)
- Swap set up manually (2GB swapfile at `/swapfile`)
- zram enabled
- GUI / display manager confirmed absent (`multi-user.target`)
- `kali-desktop-xfce` and `lightdm` purged
- Unnecessary services disabled: `bluetooth`, `ModemManager`, `cups`, `apt-daily.timer`
- **radioman repo not yet cloned to the Pi** — this is the next step

---

## What's been updated in the codebase (this session)

### `setup/install.sh` — full rewrite for Ubuntu
- OS detection at top (`$OS_ID` from `/etc/os-release`)
- **bettercap**: Kali → `apt install`; Ubuntu → downloads arm64 binary from GitHub releases
- **nexmon**: Kali → `apt install brcmfmac-nexmon-dkms`; Ubuntu → calls `install_monitor.sh` to build from source
- **libpcap**: tries `libpcap0.8` then `libpcap0.8t64` (Ubuntu 24.04 renamed it)
- **wordlists**: Kali apt package → wget from GitHub as fallback for Ubuntu
- `apt upgrade` is `DEBIAN_FRONTEND=noninteractive` (no hanging prompts on headless)
- `dphys-swapfile` skipped on Ubuntu (Raspberry Pi OS only); manual swapfile used instead
- Boot config paths: tries `/boot/firmware/config.txt` (Ubuntu) then `/boot/config.txt` (Kali/Pi OS)
- Post-install SSH instructions use `$REAL_USER` (from `$SUDO_USER`)

### `setup/install_monitor.sh` — full rewrite
- OS-aware: Kali uses apt package, Ubuntu builds nexmon from source
- Ubuntu path: installs build deps, clones nexmon to `/opt/nexmon-src`, builds BCM43430A1 patch, registers as DKMS module
- Falls back with explicit manual instructions if DKMS source directory can't be located
- Chip: `bcm43430a1`, firmware version: `7_45_41_46`

### `setup/radioman.service`
- Removed `pisugar-server.service` from `After=` and `Wants=` — PiSugar is optional, was causing startup delay when absent

### `setup/update.sh` / `setup/uninstall.sh`
- Boot config mount guarded by `[ -d /boot/firmware ]` — prevents noisy warnings on Kali
- Waveshare path updated to search multiple sub-paths + find fallback

### `daemon/capture.py`
- `wpa_cli` fallback now only fires when `nmcli` is absent — prevents it interfering on Ubuntu/Kali where NetworkManager owns the interface

### `scripts/build_llama_ubuntu.sh` (new)
- Cross-compiles llama-cli for aarch64 from Ubuntu laptop (same as WSL script, just labeled ubuntu-build)
- Usage: `bash scripts/build_llama_ubuntu.sh [radioman.local]`

---

## Feature roadmap (in progress — building one phase at a time)

Seven features planned, built in order with a check-in before each phase. Status as of this session: **Phase 1 in progress.**

1. **AI reliability** — the AI assistant wasn't running prompts in the dashboard. NOTE: live-data grounding is *already implemented* in [ai.py](daemon/ai.py) `_live_context()` — it injects network/client/capture/crack counts, security mix, busiest channels, and recent events into every prompt. The problem is inference itself failing. Fix: capture llama-cli stderr for real diagnostics, fix context-budget overflow (ctx-size vs. prompt size), surface real errors to the dashboard, add a frontend timeout.
2. **OUI + device fingerprinting** — bundle the IEEE OUI DB locally (~3MB), add a `fingerprint.py` resolver, enrich vendor lookups in `scanner.py`/`wifiscan.py`, add `device_type` columns + frontend icons. Done early because it improves data quality for every later phase.
3. **GPS + Wardrive mode** — new `gps.py` (gpsd or raw NMEA from USB dongle), `lat`/`lon`/`accuracy` columns on networks + a `wardrive_track` table, config section, Leaflet offline map view.
4. **Bluetooth scanning** — new `ble.py` (bettercap `ble.recon` or `bluetoothctl`), new `bluetooth` DB table, new dashboard view. Uses the otherwise-idle BT radio.
5. **Password intelligence** — new `passwords.py`: strength scoring, pattern detection (keyboard walks, year suffixes, vendor defaults), cross-network reuse detection. Feeds the AI analyze tab.
6. **Optional encrypted capture storage** — encrypt `.pcapng` at rest in `capture.py`, PIN-derived key shown on e-ink. Last because the crack queue needs plaintext, so it must interoperate with `cracker.py`.

### Architecture notes discovered this session
- Daemon has grown beyond the README: also `wifiscan.py` (managed-mode AP scanner, no monitor mode needed), `topology.py` (L3/VLAN via traceroute + SNMP), `netcfg.py` (WiFi join from dashboard).
- DB schema + helpers live in [db.py](daemon/db.py); add columns via the idempotent `ALTER TABLE` block in `init()`.
- API endpoints are all in [api.py](daemon/api.py) `create_app()`; shared objects passed via the `state` dict from [radioman.py](daemon/radioman.py).
- Frontend is a single [dashboard.js](web/assets/js/dashboard.js) with a 5s poll loop; `get()`/`post()` helpers have no timeout.

## Monitor mode plan

Getting monitor mode on the BCM43430A1 with Ubuntu is the main open question. Three stages:

### Stage 1 — Test native first (do this first, takes 5 min)
On the Pi after first boot:
```bash
sudo iw phy phy0 interface add mon0 type monitor
sudo ip link set mon0 up
iw dev  # check if mon0 shows type monitor
```
Ubuntu 24.04 ships a 6.x kernel. brcmfmac support has improved — this might just work. If it does, nexmon is not needed.

### Stage 2 — nexmon from source via `install_monitor.sh`
If native monitor mode fails, `install.sh` calls `install_monitor.sh` which builds the nexmon brcmfmac driver patch. The uncertainty is where nexmon puts the patched brcmfmac output — it varies between nexmon releases. The script does a `find` and prints the path so you can see what it found.

After the build, re-run the Stage 1 test to confirm.

### Stage 3 — USB WiFi adapter (fallback)
If nexmon proves unstable against Ubuntu kernel updates, use an external adapter (Alfa AWUS036ACH, rtl8812au driver). Adds hardware but eliminates nexmon maintenance forever.

---

## Critical nexmon rule — do NOT install firmware-nexmon

`firmware-nexmon` replaces Cypress firmware files and **crashes the BCM43430A1** (chip revision mismatch). Only the kernel driver patch (`brcmfmac-nexmon-dkms`) is installed. The stock Cypress firmware stays untouched. This is enforced everywhere in the scripts via `apt-mark hold firmware-nexmon`.

---

## Network layout

| Interface | IP | Configured by |
|---|---|---|
| wlan0 | dynamic or static (user's home network) | NM profile, pre-configured on SD card |
| usb0 | 10.55.0.1/24 | `install.sh` → `nmcli connection add` |

USB gadget ethernet (usb0) is the primary management interface — use it for SSH during scanning because bettercap puts wlan0 into monitor mode and drops the WiFi connection.

Host machine connects at 10.55.0.2. Scripts for USB setup:
- Windows: `scripts/win_connect.ps1` (run as Admin)
- macOS: `scripts/mac_connect.sh`
- Linux: `scripts/linux_connect.sh`

---

## Next steps

1. **Clone repo to Pi**
   ```bash
   git clone https://github.com/rightrice/radioman
   cd radioman
   sudo bash setup/install.sh
   ```

2. **Test native monitor mode** (Stage 1 above) before rebooting after install

3. **Reboot** — activates USB gadget, SPI, I2C

4. **Reconnect via USB** at `ubuntu@10.55.0.1`

5. **Verify monitor mode** — `sudo bash setup/install_monitor.sh`

6. **Edit config** — `/opt/radioman/radioman.conf` (XPLT token, bettercap credentials, display model)

7. **llama.cpp** (optional AI) — build on Ubuntu laptop: `bash scripts/build_llama_ubuntu.sh radioman.local`

---

## Key file locations (on Pi after install)

| Path | Contents |
|---|---|
| `/opt/radioman/` | Daemon, web assets, config, captures |
| `/opt/radioman/radioman.conf` | Main config (never overwritten by update.sh) |
| `/opt/radioman/captures/` | PMKID/EAPOL pcapng files |
| `/opt/radioman/wordlists/rockyou.txt` | Crack wordlist |
| `/opt/radioman/llama/llama-cli` | AI binary (if installed) |
| `/opt/waveshare-epd/` | Waveshare e-Paper library source |
| `/opt/nexmon-src/` | nexmon build tree (Ubuntu only) |

---

## PiSugar

Optional. The daemon detects PiSugar at runtime via `/tmp/pisugar-server.sock` then falls back to I2C direct. If neither is present, battery just shows as unavailable — nothing breaks.

To install PiSugar support separately after the main install:
```bash
sudo bash setup/install_pisugar.sh
```
