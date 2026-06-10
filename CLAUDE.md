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
- Swap set up manually (2GB swapfile at `/swapfile`); zram enabled
- GUI / display manager confirmed absent (`multi-user.target`)
- Unnecessary services disabled
- radioman repo **cloned to the Pi**, `setup/install.sh` has been run
- bettercap and aircrack-ng installed (aircrack built from source → `/usr/local/bin`)
- **Monitor mode: not yet confirmed working** — still the main open hardware question (see Monitor mode plan)
- **AI: Phase 1 fix is in the repo but not yet verified on the Pi** — run the verification block below after `git pull && sudo bash setup/update.sh`

---

## What's been updated in the codebase (this session)

### `setup/install.sh` — full rewrite for Ubuntu
- OS detection at top (`$OS_ID` from `/etc/os-release`)
- **bettercap**: Kali → `apt install`; Ubuntu → downloads arm64 binary from GitHub releases
- **nexmon**: Kali → `apt install brcmfmac-nexmon-dkms`; Ubuntu → calls `install_monitor.sh` to build from source
- **libpcap**: tries `libpcap0.8` then `libpcap0.8t64` (Ubuntu 24.04 renamed it)
- **wordlists**: Kali apt package → wget from GitHub as fallback for Ubuntu
- security tools loop also ensures `traceroute` + `snmp` (snmpwalk) are present for the L3 topology view
- `apt upgrade` is `DEBIAN_FRONTEND=noninteractive` (no hanging prompts on headless)
- `dphys-swapfile` skipped on Ubuntu (Raspberry Pi OS only); manual swapfile used instead
- Boot config paths: tries `/boot/firmware/config.txt` (Ubuntu) then `/boot/config.txt` (Kali/Pi OS)
- Post-install SSH instructions use `$REAL_USER` (from `$SUDO_USER`)

### `setup/install_monitor.sh` — full rewrite
- OS-aware: Kali uses apt package, Ubuntu builds nexmon from source
- Ubuntu path: installs build deps, clones nexmon to `/opt/nexmon-src`, builds BCM43430A1 patch, registers as DKMS module
- **aarch64 toolchain fix**: nexmon ships a prebuilt `arm-none-eabi-gcc` built for armv7l (32-bit), which can't execute on arm64 Ubuntu. The script installs the system `gcc-arm-none-eabi` and symlinks the bundled toolchain binaries to the system ones before building.
- Picks the patched brcmfmac driver source closest to (but not newer than) the running kernel
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

### `daemon/ai.py` + `web/assets/js/dashboard.js` — Phase 1 AI reliability fix
- **Root cause:** llama-cli's stderr (the real error) was merged into the PTY and discarded, so every failure surfaced as a generic "Inference failed."
- `_infer()` now captures stderr on a **separate pipe**, returns a diagnostic dict (`{"text"}` or `{"error"}`), and a new `_diagnose_stderr()` maps stderr to a real cause (rejected CLI flag, model-load failure, wrong CPU arch, OOM). Removed `--log-disable` so stderr carries those logs.
- `CTX_SIZE` 1024 → 2048, and `_build_prompt()` trims oldest turns if the prompt would overflow the window (an "Analyze Networks" prompt + live context could exceed 1024 and produce nothing).
- Completion detection no longer relies solely on the old `"Generation:"` log line — falls back to process-exit/EOF.
- Frontend: `post()` takes an optional timeout; AI calls use a 315s `AbortController` (just above the 300s daemon timeout) with distinct "timed out" vs "network error" messages.

---

## Feature roadmap (in progress — building one phase at a time)

Six features planned (the user's list double-counted GPS), built in order with a check-in before each phase. User chose **"go in order, one at a time."** Status: **Phases 1, 2 & 3 done (code complete, need Pi verification). Phase 4 is next.**

1. ✅ **AI reliability** — DONE in code (see `daemon/ai.py` + `dashboard.js` notes above). Live-data grounding was *already implemented* in `_live_context()`; the blocker was inference failing silently. Still needs verifying on the Pi — see "Verifying Phase 1" below.
2. ✅ **OUI + device fingerprinting** — DONE in code. New `daemon/fingerprint.py` `device_type_for(mac, vendor, ssid, is_ap)` classifies a coarse device type (router/phone/computer/iot/tv/printer/camera/voip/wearable/gaming/sbc/unknown) from the resolved vendor string + SSID hints + randomized-MAC detection. **Decision:** did NOT bundle a 3MB OUI file — vendor lookup already works via nmap's `nmap-mac-prefixes` (a dependency) in `scanner.py` `_load_oui`, so `fingerprint.py` classifies that vendor string instead. Wired into `radioman.py` `_on_network`/`_on_client`/`_on_host`; `device_type` column added to `networks`/`clients`/`hosts` (idempotent `ALTER TABLE`); `dashboard.js` shows a `deviceTag()` icon in the Networks/Clients/LAN-Hosts tables.
3. ✅ **GPS + Wardrive mode** — DONE in code. New `daemon/gps.py` `GPSReader(mode, device, baud)` with two backends: **gpsd** (python3-gps module, else a raw JSON socket to 127.0.0.1:2947) and **serial** (raw NMEA `$GxGGA`/`$GxRMC` via pyserial). Thread-safe `current_fix()` → `{fix, lat, lon, alt, accuracy, speed, ts}`; degrades to `fix:0` if no gpsd/pyserial/device. DB: `lat`/`lon`/`gps_accuracy`/`gps_rssi` columns on `networks` + a `wardrive_track` table (idempotent `ALTER TABLE`). **Decision:** each AP is stamped at its **strongest-RSSI** position — `db.stamp_network_gps()` only overwrites when `rssi >= gps_rssi` (a separate column from the live `rssi`, so the "best" reference survives later weaker sightings). Wired into `radioman.py` `_on_network` (stamp) + a `_gps_loop` breadcrumb thread (records a `wardrive_track` point every `track_interval`s when moved >~1m). `[gps]` config section added. API: `GET /api/wardrive` (`{networks, track, fix, enabled}`) + `DELETE /api/wardrive/track`. Frontend: new **Map** nav tab → `viewMap()`/`drawMap()` using **Leaflet** (loaded from unpkg CDN in `index.html`, OSM tiles) — APs as circle-markers coloured by security, the track as a polyline, current position in cyan; falls back to a friendly message if `L` is undefined (offline). Works off the always-on managed-mode `wifiscan.py` — **does not need monitor mode**. Note: Leaflet/tiles load in the *viewer's browser* (same CDN assumption as the Google-Fonts `<link>`), not on the Pi. XPLT sync is unaffected (`_network_row()` whitelists columns, so lat/lon aren't pushed).
4. **Bluetooth scanning** — new `ble.py` (bettercap `ble.recon` or `bluetoothctl`), new `bluetooth` DB table, new dashboard view. Uses the otherwise-idle BT radio.
5. **Password intelligence** — new `passwords.py`: strength scoring, pattern detection (keyboard walks, year suffixes, vendor defaults), cross-network reuse detection. Feeds the AI analyze tab.
6. **Optional encrypted capture storage** — encrypt `.pcapng` at rest in `capture.py`, PIN-derived key shown on e-ink. Last because the crack queue needs plaintext, so it must interoperate with `cracker.py`.

### Architecture notes (how to add a feature)
- Daemon has grown beyond the README: also `wifiscan.py` (managed-mode AP scanner, no monitor mode needed), `topology.py` (L3/VLAN via traceroute + SNMP), `netcfg.py` (WiFi join from dashboard).
- DB schema + helpers live in [db.py](daemon/db.py); add columns via the idempotent `ALTER TABLE` block in `init()`, and add `get_*`/`upsert_*` helpers alongside the existing ones.
- API endpoints are all in [api.py](daemon/api.py) `create_app()`; shared objects (engines, db_path, config) are passed via the `state` dict assembled in [radioman.py](daemon/radioman.py) `__init__` and `start()`.
- New daemon subsystems are instantiated in `Radioman.__init__`, started in `Radioman.start()`, added to `self._state`, and stopped in `Radioman.stop()`.
- Frontend is a single [dashboard.js](web/assets/js/dashboard.js): a 5s `poll()` loop, a `currentView` switch in `fetchViewData()`, per-view `view*()` render functions, and nav buttons in [index.html](web/index.html). `post()` now takes an optional timeout (ms).
- Config is INI via `configparser`; sections are read in `Radioman.__init__` and the example lives in [config/radioman.conf.example](config/radioman.conf.example). `api.py` `_save_conf()` persists settings changed from the dashboard.

### Verifying Phase 1 on the Pi (do this before/while starting Phase 2)
```bash
cd ~/radioman && git pull && sudo bash setup/update.sh
file /opt/radioman/llama/llama-cli          # must say ARM aarch64
# run with radioman's exact flags:
/opt/radioman/llama/llama-cli \
  --model /opt/radioman/models/granite-4.0-350m-Q4_K_M.gguf \
  --ctx-size 2048 --threads 4 --n-predict 32 -no-cnv --no-display-prompt \
  --prompt "Say hello in five words."
sudo systemctl restart radioman
```
If a flag is rejected, the new `_diagnose_stderr()` will now name it in the dashboard — adjust the flag list in `ai.py` `_infer()`. If it generates text, the AI tab should work.

### Verifying Phase 2 on the Pi
After `git pull && sudo bash setup/update.sh && sudo systemctl restart radioman`:
- DB migrates automatically (the `ALTER TABLE … device_type` lines are idempotent; existing rows backfill on next sighting).
- Networks/Clients/LAN-Hosts tables should show a device-type emoji next to each row. Run the internal WiFi scan (it's always on via `wifiscan.py`) or an nmap host scan to populate, then check the icons.
- Classification is a best-effort hint; tune the rule lists in `daemon/fingerprint.py` (`_VENDOR_RULES` / `_SSID_RULES`) as needed.

### Verifying Phase 3 on the Pi
After `git pull && sudo bash setup/update.sh && sudo systemctl restart radioman`:
- DB migrates automatically (the `lat`/`lon`/`gps_accuracy`/`gps_rssi` + `wardrive_track` lines are idempotent).
- The **Map** tab works immediately — with `[gps] mode = off` (default) it just shows an empty world map and a "GPS: disabled" badge. Nothing else breaks without a dongle.
- To actually wardrive, attach a USB GPS dongle and set `[gps]` in `/opt/radioman/radioman.conf`:
  - `mode = gpsd` (then `sudo apt install gpsd gpsd-clients python3-gps`, point gpsd at the device) **or** `mode = serial` + `device = /dev/ttyACM0` (then `sudo apt install python3-serial`).
  - Confirm a fix: gpsd → `gpsmon`/`cgps`; serial → `cat /dev/ttyACM0` should show `$GPGGA…` lines.
- With a fix, the badge turns teal and shows lat/lon ±accuracy; APs seen by `wifiscan.py` get stamped and appear as coloured dots; the breadcrumb polyline grows as you move. **No monitor mode needed** — managed-mode scanning is enough.
- Leaflet + OSM tiles load from a CDN in the *viewer's browser*, so the laptop viewing the dashboard needs internet (the Pi does not). Offline → a fallback message instead of a crash.

### Phase 4 starting point (Bluetooth scanning)
- New `daemon/ble.py`: prefer bettercap's `ble.recon` (already a dependency, REST API like `capture.py`) else fall back to `bluetoothctl`/`hcitool lescan`. Expose discovered devices (mac, name, vendor, rssi, type) via a callback like `_on_network`.
- DB: new `bluetooth` table (mac PK, name, vendor, rssi, device_type, first_seen, last_seen) + `upsert_ble`/`get_ble` helpers; reuse `fingerprint.device_type_for` for classification.
- Wire a `BLEScanner` into `radioman.__init__`/`start()`/`stop()` + `self._state["ble"]`; `/api/bluetooth` endpoint; new **Bluetooth** nav tab + `viewBluetooth()`.
- The BT radio (BCM43430A1 shares the chip; `hci0` was up in the service list) is otherwise idle, so this is free signal. Watch for contention if BT and WiFi monitor mode ever run together.

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
