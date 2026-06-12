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
| Wireless | **Synaptics 43436s** (this board's actual chip — NOT a nexmon-supported BCM43430A1). No internal monitor mode; capture/deauth/rogue-AP need a **USB adapter**. See [[project_pi-zero-2w-wifi-chip]]. |
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

## Multi-board support — Pi Zero 2W **and** Pi 5 (16GB, AI HAT+)

The project now adapts to the host board. `daemon/hwinfo.py` detects board model (`/proc/device-tree/model`), core count, RAM, and AI HAT+/Hailo presence, and returns `recommended_ai()` params.

**AI auto-scales** ([ai.py](daemon/ai.py)): threads = core count (capped), and ctx/n_predict/timeout scale with RAM — Zero 2W class: ctx 2048 / 256 tok / 300s; **Pi 5 class (≥4GB, ≥4 cores): ctx 4096 / 512 tok / 120s**. Everything is overridable via a new `[ai]` config section (`llama_cli`, `model`, `threads`, `ctx_size`, `n_predict`, `timeout`) — all blank by default = auto. `/api/ai/status` now includes a `hardware` block; the dashboard AI tab shows board/cores/RAM + params.

**`setup/install_ai.sh` is board-aware** (user chose the "~3B balanced" tier): Zero 2W (<4GB) → **Granite 4.0 350M** (~230MB); Pi 5 (≥4GB) → **Granite 3.3 2B Instruct** (~1.5GB). Both are IBM Granite so `ai.py`'s chat template (`<|user|>`/`<|assistant|>`) stays valid — **if you swap to a non-Granite model (e.g. Llama), update `_build_prompt` in ai.py to match its template.** Override with `sudo MODEL_REPO=<hf/repo> MODEL_FILENAME=<file.gguf> bash setup/install_ai.sh`; a HEAD-check falls back to the small model if the chosen one is unreachable. The script writes the real `model =` path into the conf's `[ai]` section via configparser. **Everything stays 100% on-device — the LLM is a local `llama-cli` subprocess, zero network calls (user requirement).**

**Two AI HAT generations — know which one:**
- **AI HAT+ (Hailo-8/8L)** — vision/CNN NPU. Cannot run LLMs (no llama.cpp backend, wrong architecture). Not used by radioman's assistant.
- **AI HAT+ 2 (Hailo-10H, released 2026-01-15)** — 40 TOPS INT4, **8GB on-board RAM**, purpose-built for GenAI and **runs LLMs on-device**. radioman **uses it for the assistant** when available. (Earlier notes here said "the HAT can't run the LLM" — that was true for the Hailo-8, wrong for the AI HAT+ 2; corrected.)

**Hailo-10H integration ([ai.py](daemon/ai.py)):** the HAT runs an LLM via **hailo-ollama**, a local **Ollama-compatible HTTP server** (loopback `127.0.0.1` — no internet at runtime). `[ai] backend = auto|llama|hailo`: **auto** uses the HAT if present + the server answers, else the CPU. The Hailo path POSTs structured messages to `/api/chat` (server applies the model's chat template, so no Granite-template building). ~30–50 tok/s vs ~2–5 on CPU. `setup/install_hailo.sh` sets it up (PCIe Gen3, `hailo-all`, hailo-ollama, model pull, sets `[ai] backend=hailo`).

**US-origin models only ([[feedback-us-software]]):** Hailo supports Llama 3.2 1B (Meta), Qwen 2.5 (Alibaba), DeepSeek R1 (China) — the user requires **US-company** software, so the HAT runs **Llama 3.2 1B (Meta)** and the CPU path runs **IBM Granite**. The HAT can't run our Granite GGUF (only Hailo-compiled models), so Granite stays the CPU model. Do not propose Qwen/DeepSeek.

**Runtime is 100% on-device** (user requirement). The only network use is the one-time install download (Hailo packages + ~2GB model); after that it's airgap-clean.

**`setup/install.sh` is board-aware:** swap + zram are created **only on low-RAM boards** (<1536MB) — skipped on the Pi 5; the USB-gadget (`usb0`/dwc2/g_ncm) management crutch is **skipped on the Pi 5** (it has real Gigabit Ethernet — manage over the LAN). The Pi 5's internal radio (CYW43455) **also can't do monitor mode**, so capture/deauth/rogue-AP still need the Alfa (`install_alfa.sh`) — unchanged. The official active cooler is firmware-managed; no `arm_freq` cap needed (that was a Zero 2W mitigation).

---

## ⏩ Session handoff — continuing on the laptop (2026-06-10)

**All of this session's work is in the working tree and UNCOMMITTED.** The user handles all git commits ([[feedback_git]]) — review and commit on the laptop. Nothing here has been verified on the Pi yet; it's code-complete + locally tested (`py_compile`, `node --check`, and per-feature Python unit tests on the dev machine).

**Shipped this session (code-complete, needs Pi verification):**
- **Save/Delete + Purge** — per-row delete on Networks/Clients/Bluetooth + 1/3/7/15-day purge (shared `wirePurge()` helper). `db.delete_*`/`purge_stale_*`, `DELETE`/`purge` API routes.
- **Phase 3 — GPS + Wardrive** (`daemon/gps.py`, Leaflet Map tab). See the Phase 3 entry.
- **Phase 4 — Bluetooth** (`daemon/ble.py`, Bluetooth tab). See the Phase 4 entry.
- **Active / offensive testing track** — `daemon/authz.py` (deny-by-default chokepoint), `daemon/attack.py` (gated single-target deauth), `daemon/rogueap.py` (arm-gated evil twin), the `scope`/`rogue_*` tables, the **Active** dashboard tab, `[offensive]` config (off by default). See the "Active / offensive testing" section.

**New files added this session:** `daemon/gps.py`, `daemon/ble.py`, `daemon/authz.py`, `daemon/attack.py`, `daemon/rogueap.py`. (`daemon/fingerprint.py` already existed from Phase 2.)

**Next up:** All 6 roadmap phases are code-complete — needs end-to-end **Pi verification** (esp. the parts needing a USB adapter: capture/deauth/rogue-AP). Optional follow-up: wire rogue-AP/deauth-forced handshakes into the existing crack queue; e-ink vault-fingerprint indicator (deferred — see Phase 6 note).

**To run the dashboard on the laptop** (no Pi hardware needed — everything degrades gracefully): `cd daemon && python3 radioman.py ../config/radioman.conf.example` then open `http://127.0.0.1:8080`. GPS/BT/display/PiSugar all report "unavailable" cleanly; the Active tab shows OFFENSIVE MODE OFF unless you set `[offensive] enabled=true` in a local conf copy.

## Current Pi status (as of 2026-06-10)

- Ubuntu Server 24.04 LTS arm64 flashed and booted; headless (`multi-user.target`); 2GB `/swapfile` + zram; repo cloned, `setup/install.sh` run; bettercap + aircrack-ng installed.
- **Recurring wlan0 loss on every power cycle — FIXED PERMANENTLY (this session).** The nexmon `brcmfmac` DKMS module kept coming back each boot (DKMS module in the tree + `install.sh` rebuilding it), failing to bind the Synaptics 43436s (`probe ... error -110`) → no `wlan0`. **Two fixes:** (1) new **`setup/fix_wlan0.sh`** removes the nexmon DKMS module + source + any patched `brcmfmac.ko`, restores the stock driver, rebuilds `depmod` + initramfs, reloads, and verifies — run it once on the Pi, then reboot to confirm. (2) **`install.sh` no longer builds nexmon** (it was the source of recurrence) — it now skips it with guidance to use the Alfa. `install_monitor.sh` is gated behind `I_HAVE_NEXMON_CHIP=1` so a manual run can't silently re-break wlan0. Reconnect after fix with `netplan apply`/`wpa_supplicant` (this box has no `nmcli`).
- **Monitor mode on the internal radio is confirmed IMPOSSIBLE** — the chip is a Synaptics 43436s, not nexmon-supported ([[project_pi-zero-2w-wifi-chip]]). nexmon DKMS is removed and should stay removed. **All capture/deauth/rogue-AP features therefore require a USB WiFi adapter** (Monitor mode plan, Stage 3). The internal radio still does managed-mode scanning fine (`wifiscan.py`, always-on).
- **Thermal throttling is an open hardware issue.** Idle was 83.8°C with `throttled=0x60006` (arm-freq-capped + currently-throttled) — the SoC has no heatsink. Fixes: add a heatsink (primary), and optionally cap `arm_freq=800` in `/boot/firmware/config.txt` + disable `udisks2`/`serial-getty@ttyS0`. Flask is still the dev server (werkzeug) — fine, not the heat source.
- **AI: Phase 1 fix in repo, not yet verified on the Pi** — see "Verifying Phase 1".

---

## Codebase changes from the earlier Ubuntu-migration session

(Baseline context — predates the feature work in the handoff above.)

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

Six features planned (the user's list double-counted GPS), built in order with a check-in before each phase. User chose **"go in order, one at a time."** Status: **All 6 phases done (code complete, need Pi verification).**

1. ✅ **AI reliability** — DONE in code (see `daemon/ai.py` + `dashboard.js` notes above). Live-data grounding was *already implemented* in `_live_context()`; the blocker was inference failing silently. Still needs verifying on the Pi — see "Verifying Phase 1" below.
2. ✅ **OUI + device fingerprinting** — DONE in code. New `daemon/fingerprint.py` `device_type_for(mac, vendor, ssid, is_ap)` classifies a coarse device type (router/phone/computer/iot/tv/printer/camera/voip/wearable/gaming/sbc/unknown) from the resolved vendor string + SSID hints + randomized-MAC detection. **Decision:** did NOT bundle a 3MB OUI file — vendor lookup already works via nmap's `nmap-mac-prefixes` (a dependency) in `scanner.py` `_load_oui`, so `fingerprint.py` classifies that vendor string instead. Wired into `radioman.py` `_on_network`/`_on_client`/`_on_host`; `device_type` column added to `networks`/`clients`/`hosts` (idempotent `ALTER TABLE`); `dashboard.js` shows a `deviceTag()` icon in the Networks/Clients/LAN-Hosts tables.
3. ✅ **GPS + Wardrive mode** — DONE in code. New `daemon/gps.py` `GPSReader(mode, device, baud)` with two backends: **gpsd** (python3-gps module, else a raw JSON socket to 127.0.0.1:2947) and **serial** (raw NMEA `$GxGGA`/`$GxRMC` via pyserial). Thread-safe `current_fix()` → `{fix, lat, lon, alt, accuracy, speed, ts}`; degrades to `fix:0` if no gpsd/pyserial/device. DB: `lat`/`lon`/`gps_accuracy`/`gps_rssi` columns on `networks` + a `wardrive_track` table (idempotent `ALTER TABLE`). **Decision:** each AP is stamped at its **strongest-RSSI** position — `db.stamp_network_gps()` only overwrites when `rssi >= gps_rssi` (a separate column from the live `rssi`, so the "best" reference survives later weaker sightings). Wired into `radioman.py` `_on_network` (stamp) + a `_gps_loop` breadcrumb thread (records a `wardrive_track` point every `track_interval`s when moved >~1m). `[gps]` config section added. API: `GET /api/wardrive` (`{networks, track, fix, enabled}`) + `DELETE /api/wardrive/track`. Frontend: new **Map** nav tab → `viewMap()`/`drawMap()` using **Leaflet** (loaded from unpkg CDN in `index.html`, OSM tiles) — APs as circle-markers coloured by security, the track as a polyline, current position in cyan; falls back to a friendly message if `L` is undefined (offline). Works off the always-on managed-mode `wifiscan.py` — **does not need monitor mode**. Note: Leaflet/tiles load in the *viewer's browser* (same CDN assumption as the Google-Fonts `<link>`), not on the Pi. XPLT sync is unaffected (`_network_row()` whitelists columns, so lat/lon aren't pushed).
4. ✅ **Bluetooth scanning** — DONE in code. New `daemon/ble.py` `BLEScanner` streams sightings from BlueZ **`bluetoothctl`** in interactive mode (`power on` + `scan on`, parse `[NEW]`/`[CHG]` Device lines → MAC/name/RSSI). **Decision:** chose bluetoothctl over bettercap `ble.recon` because bettercap only runs during a Wi-Fi capture session and is bound to `mon0`, whereas the BT controller (`hci0`) is independent — so this gives always-on discovery on the otherwise-idle radio. Pure, tested parser `parse_btctl_line()`; per-device emit debounce (`EMIT_INTERVAL=15s`) so RSSI churn doesn't hammer the DB; degrades cleanly if `bluetoothctl`/controller absent. Classification via new `fingerprint.ble_type_for(mac, vendor, name)` + `_BLE_NAME_RULES` (adds an **`audio`** type 🎧 for earbuds/headsets; names do most of the work since BLE addresses are often randomized with no OUI). DB: new `bluetooth` table (mac PK, name, vendor, rssi, device_type, first/last_seen) + `upsert_bluetooth`/`get_bluetooth`/`delete_bluetooth`/`purge_stale_bluetooth`; a `bluetooth` count was added to `get_stats`. Wired into `radioman.py` (`_on_ble`, init/start/stop, `state["ble"]`, `vendor_lookup=scanner._vendor_for`). `[bluetooth] mode = auto|bluetoothctl|off`. API: `GET /api/bluetooth` + `DELETE /api/bluetooth/<mac>` + `POST /api/bluetooth/purge`. Frontend: new **Bluetooth** nav tab → `viewBluetooth()` (same row-Delete + 1/3/7/15-day Purge UI as Networks, refactored into a shared `wirePurge()` helper) + a Bluetooth KPI on the Overview.
5. ✅ **Password intelligence** — DONE in code. New `daemon/passwords.py` (pure, no hardware): `score()` (length/charset/entropy + rating, with a small embedded common-password list that caps guessable keys at "weak"), `detect_patterns()` (keyboard walks, year/date suffixes, word+digits, all-lower, repeated chars, phone-like, common-word, contains-SSID), `default_shape()` (ISP/router factory shapes — hex requires a hex *letter* so pure-digit PINs don't false-positive), `find_reuse()` (same key across BSSIDs/SSIDs), `analyze()` (aggregate + deterministic recommendations), `summarize()` (literal-password-free block for AI grounding). Wired: `ai.py` `analyze_passwords(items)` now grounds on `passwords.summarize()` instead of raw keys; `api.py` passes full capture dicts + new `GET /api/passwords`; dashboard **Captures** tab shows a `passwordIntelPanel()` (works without the LLM). **All passwords masked** in API/UI output (verified no literal leak). Unit-tested + Flask-test-client tested.
6. ✅ **Optional encrypted capture storage** — DONE in code. New `daemon/vault.py` encrypts `.pcapng` at rest via the system `openssl enc -aes-256-cbc -pbkdf2` (no new Python dep). Two config-selectable key modes (`[storage] key_mode`): **config** (passphrase in conf → key always available, auto-crack unattended) and **pin** (no passphrase on disk → unlock from the dashboard each boot, key in memory only). `_on_capture` encrypts on arrival; the crack queue and the download endpoint get a transparent plaintext view via `vault.plaintext()` (decrypt-to-temp, wiped after). Unlock migrates pre-unlock plaintext + reconciles the DB + re-queues uncracked captures. Locked encrypted download → 423. Fingerprint shown in the dashboard vault banner + daemon log. **Deferred:** e-ink fingerprint glyph (fixed 250×122 layout, couldn't verify render — dashboard banner covers the need).

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

### Verifying Phase 4 on the Pi
After `git pull && sudo bash setup/update.sh && sudo systemctl restart radioman`:
- DB migrates automatically (new `bluetooth` table created by `CREATE TABLE IF NOT EXISTS`).
- Prereqs: `bluez` installed (`bluetoothctl` on PATH) and the controller unblocked — `rfkill unblock bluetooth`, `bluetoothctl power on`. We saw `hci0` up + Bluetooth unblocked in the earlier service dump, so this should already be the case.
- Sanity-check the source directly: `bluetoothctl` → `scan on` should stream `[NEW]/[CHG] Device …` lines. That's exactly what `ble.py` parses.
- In the dashboard, the new **Bluetooth** tab should fill within a minute (phones, earbuds, watches, TVs nearby), each with a device-type icon; the Overview gains a **Bluetooth** KPI. Per-row Delete + Purge work like the Networks view.
- Runs continuously and independently of Wi-Fi capture (separate `hci0` controller) — **no monitor mode needed**. If the combo chip shows coexistence interference while bettercap holds `mon0`, consider pausing BLE during capture (add a `should_pause=lambda: self.capture.scanning` like `wifiscan.py`); not done yet because they're nominally independent.
- `mode = off` in `[bluetooth]` disables it entirely.

### Phase 6 (Encrypted capture storage) — DONE, how it's wired
- `daemon/vault.py` — `Vault(enabled, mode, passphrase, captures_dir)`. Crypto = system `openssl enc -aes-256-cbc -pbkdf2 -salt`, passphrase piped on stdin (never argv). CBC = confidentiality at rest (threat model: lost/seized device), not authentication.
- Key modes (`[storage] key_mode`): **config** (passphrase from conf, key always loaded) / **pin** (no passphrase on disk; `POST /api/vault/unlock` sets an in-memory key; `lock` clears it).
- Flow: bettercap writes plaintext → `capture.py _poll` detects → `radioman._on_capture` calls `vault.encrypt_file()` (→ `<name>.enc`, plaintext shredded) and inserts the row with `encrypted=1`. The crack queue (handed the vault) and the download endpoint use `vault.plaintext(path)` (context manager: decrypt to a temp file, shred on exit). Locked + encrypted download → HTTP 423.
- Unlock reconciles: `vault.encrypt_pending()` migrates pre-unlock plaintext, `db.sync_capture_encryption()` fixes rows to disk reality, then uncracked captures are re-queued. Config mode does the same on startup in `Radioman.start()`.
- DB: `captures.encrypted` column (idempotent ALTER) + `update_capture_file`/`sync_capture_encryption` helpers. `insert_capture()` gained an `encrypted` arg.
- API: `GET /api/vault`, `POST /api/vault/{unlock,lock}`. Frontend: vault banner + unlock/lock + 🔒 badge on encrypted captures (in the Captures tab).
- **Deferred:** e-ink fingerprint glyph — the e-ink frame is a fixed 250×122 layout I couldn't render-test, so the fingerprint is shown in the dashboard banner + logged at unlock instead.
- Verified: openssl round-trip, vault unit tests (config/pin/disabled/locked), and a Flask-test-client end-to-end (encrypt-at-rest, decrypt-to-temp for crack, in-memory decrypt download, pin lock→unlock→migrate→reconcile→requeue, locked download 423).

### Phase 5 (Password intelligence) — DONE, how it's wired
- `daemon/passwords.py` is pure/offline. Entry points: `analyze(items)` → structured dict for `/api/passwords` + the dashboard panel; `summarize(items)` → literal-password-free text for AI grounding; plus `score`/`detect_patterns`/`default_shape`/`find_reuse`/`is_common`/`mask` as building blocks.
- `items` are cracked capture dicts (`{ssid, bssid, password}`) — i.e. `[c for c in db.get_captures() if c.get("password")]`.
- On-demand analysis (no DB migration) — chose this over `pw_score` columns to avoid churn.
- All output masks the literal key (`mask()` = first 2 chars + stars), verified no leak in the API payload.

## Active / offensive testing (authorized engagements only)

A separate track from the 6-feature roadmap. **Authorized pentest use only** — the entire point of the design is that it *cannot* act outside an explicit allowlist. Built one capability at a time, safeguard first.

### The safeguard (the centerpiece — `daemon/authz.py`)
Every active action funnels through one chokepoint, `AuthzEngine.is_authorized(target, kind)`, which is **fail-closed / deny-by-default**:
1. **Deploy-time master switch** — `[offensive] enabled` defaults to `false`. When false the active-testing API returns 403 and the engines refuse to act, so a freshly-flashed device is inert.
2. **Per-target allowlist** — the `scope` table (Rules of Engagement). Nothing is authorized unless its exact target was explicitly added (with a required `authref` authorization reference). MAC targets are normalized upper-case. *(User chose "allowlist only" — no separate per-engagement arm switch for deauth; the deploy-time flag + allowlist + audit are the guardrails.)*
3. **Audit trail** — every decision, ALLOW *and* DENY, is written to `events` (levels `active`/`denied`) and surfaced in the dashboard's **Active** tab. `db.get_audit()`.

### Capability 1 — targeted deauth (DONE in code, `daemon/attack.py`)
`AttackEngine.deauth(bssid, client="", reason="")` — bounces the clients of a single in-scope AP (delegates to bettercap `wifi.deauth <mac>` via the existing `CaptureEngine.send_cmd`) so they reconnect and we capture the handshake. Safety properties (all unit-tested):
- **Single-target only** — broadcast (`ff:ff:ff:ff:ff:ff`/`00:..`/`*`) is explicitly refused. No "deauth all" / mass-DoS mode exists, by design.
- Target (and any named client) must pass `is_authorized()`; denials are audited and nothing transmits.
- **Rate-limited** per target (`[offensive] deauth_min_interval`, default 5s) so it can't become a sustained flood.
- Requires bettercap/monitor mode active to transmit. (NB: monitor mode needs a USB adapter on this hardware — internal Synaptics 43436s can't; see [[project_pi-zero-2w-wifi-chip]]. The auth/gating logic is hardware-independent and fully testable without it.)

Wiring: `[offensive]` config → `radioman.py` builds `AuthzEngine`+`AttackEngine`, adds `authz`/`attack`/`offensive_enabled` to `state`. API: `GET /api/offensive/status`, `GET/POST/DELETE /api/scope`, `POST /api/attack/deauth` (403 if disabled), `GET /api/audit`. Frontend: **Active** nav tab (red) → `viewActive()` with a Rules-of-Engagement banner, scope add/list/remove (kind = bssid|client|ssid|ip + required auth ref), a per-AP **Deauth** button (enabled only when offensive+scanning), and the audit feed.

### Ergonomic scoping (DONE — so it's field-usable, not per-MAC tedium)
`authz.is_authorized()` now matches beyond an exact allowlist hit: a **BSSID is authorized if its SSID is in scope** (`db.ssid_for_bssid`), and an **IP is authorized if it's inside any scoped CIDR** (`ipaddress`). So you authorize a whole client network once (one `ssid` or `ip/CIDR` entry) and operate within it. `POST /api/scope/bulk` parses a pasted RoE list (auto-detect MAC→bssid, IP/CIDR→ip, else ssid; optional `kind:` prefix; shared authref). Dashboard **Active** tab leads with an **"Authorized targets — live"** table (discovered APs that are in scope by BSSID or SSID) each with a one-tap **Deauth** — the Pineapple-style flow.

### Capability 2 — rogue AP / evil-twin (DONE in code, `daemon/rogueap.py`)
Built with the extra guardrail, since an allowlist can't bound who associates. `RogueAPEngine` clones an **in-scope SSID** via hostapd + dnsmasq on a separate AP interface (`[offensive] ap_interface`, default `wlan1` — needs a USB adapter; internal radio can't AP), with a captive portal:
- **Arm-gated**: `arm(ssid, authref, acknowledge, capture_creds)` requires offensive mode on, `authz.is_ssid_authorized(ssid)` (SSID must be a scope entry), an authref, AND an explicit `acknowledge`. Nothing starts until armed; arm/start/stop/credential-submission all audited.
- **Captive portal defaults to a benign authorized-assessment notice** that only logs associations (`rogue_clients`). The credential-phishing page is a separate **`capture_creds` opt-in** (off by default), surfaced in the UI with a red toggle + an extra confirm; submissions go to `rogue_captures`, **masked** in `/api/rogueap/loot` (full value stays in DB for the report, mirroring crack masking). No stealth/anti-logging options exist.
- Degrades cleanly: missing hostapd/dnsmasq/AP-iface → clear error, no crash (gating logic is hardware-independent and unit-tested).
- API: `GET /api/rogueap/status`, `POST /api/rogueap/{arm,disarm,start,stop}`, `GET/DELETE /api/rogueap/loot` (all 403 when offensive disabled). Frontend: a rogue-AP control card (in-scope SSID dropdown, channel, creds toggle, Arm→Start/Stop) + a loot table.

**Known limitation (documented for the user):** even in-scope, a cloned-SSID AP is RF-indiscriminate — bystanders in range can associate. The arm+ack is the operator's attestation of authorization; the benign-by-default portal limits blast radius. **What's intentionally NOT built:** broadcast/mass deauth, beacon/karma floods, or any untargeted disruption — those are mass-targeting/DoS and were declined regardless of the README disclaimer.

### Friction reducers (DONE in code — keep the boundary, cut the tedium)
The user asked to "remove all safeguards" to spin up rogue APs as needed. We **kept the `authz.py` boundary** (it's what separates an authorized evil-twin from harvesting bystanders' creds) and instead removed the *friction* of authorizing fast. None of this touches the chokepoint logic — `is_authorized()` still calls `is_in_scope()`, which is now strictly tighter.
- **One-tap authorize** — the Active tab has an **"In range — not in scope"** table; each discovered AP has `+ BSSID` / `+ SSID` buttons that add it to scope using the shared **Engagement context** (authref + label + TTL set once at the top). Attestation (authref) is still required.
- **Engagement profiles** — scope entries carry an `engagement` label; engagement chips show counts with an **End** (×) button that bulk-clears that engagement's scope (`DELETE /api/scope/engagement`). `GET /api/scope/engagements`.
- **Auto-expiry** — scope entries carry an `expires` (from a TTL dropdown). `db.is_in_scope`/`get_scope_targets` exclude expired rows, so expiry is **fail-closed** (auto-deny) — strictly safer. `radioman.py` purges expired rows on the ~10-min cleanup tick (`db.purge_expired_scope`).
- **"My Lab"** — a `lab_targets` table of the operator's own networks + an **Apply lab to scope** button (one click adds them all, authref `owned-lab`, engagement `lab`). API: `GET/POST/DELETE /api/lab`, `POST /api/lab/apply`.
- DB: `scope` gained `engagement`+`expires` (idempotent ALTER); new `lab_targets` table; helpers `get_engagements`/`clear_engagement`/`purge_expired_scope`/`add_lab_target`/`get_lab_targets`/`remove_lab_target`. All unit-tested (db + Flask test-client + authz boundary incl. expiry). `authz.py` unchanged.

### Dashboard UX redesign (2026-06-11) — Active tab + responsive pass
The Active tab had grown to a **9-panel wall**; restructured (user asked for "all three" of sub-tabs/collapsible/guided, "usable across all platforms"). All in `dashboard.js` `viewActive()` + `radioman.css`; **no backend/endpoint changes** — same panels, re-routed.
- **Sub-tabs** (`activeTab` module var, persists across polls): **Targets** (live in-scope + "in range, not in scope" authorize tables), **Rogue AP** (control + loot), **Scope** (manual add + bulk + My Lab), **Audit**. Switching re-renders instantly from `activeCache` (no refetch). Handlers are id/class-guarded so only the mounted sub-view's controls bind.
- **Guided stepper** strip (Enable → Scope a target → Act) with done/current/todo states — the "guided" feel without a rigid wizard.
- **Engagement context** is a collapsible `<details>` (`.rm-acc`) rendered on *every* sub-tab, so `#rmEngAuth` is always in the DOM and one-tap authorize works from the Targets tab.
- **Responsive/field**: `.rm-nav` becomes a horizontal scroll-strip ≤760px; field tables (`.rm-cards-sm` + `data-label` on `<td>`s) collapse to stacked cards ≤600px; touch-sized controls (min 40–44px).
- **Poll re-render fixes** (the Active view is stateful, so the 5s poll mustn't fight the user):
  - `renderMain()` skips the re-render while a form field in `#rmMain` has focus (was wiping in-progress typing).
  - Active view re-renders only when a **per-sub-tab data signature** (`activeSig()`) changes — so signal-strength churn on `nets` no longer flickers the Scope/Rogue/Audit tabs (Targets still tracks live RSSI).
  - `<details>` open-state is preserved across re-renders via `accState` + `accOpen()` + a `toggle` listener (data-acc key) — fixes expanded panels snapping shut on poll.
- Verified by a stubbed-DOM Node harness that executes `viewActive()` for all four sub-tabs (no Flask locally). To re-run the dashboard on the laptop you still need `pip install flask flask-cors requests`.

### `setup/install_alfa.sh` (new) — external adapter driver (dual-model)
Required for all active features — the internal Synaptics 43436s can't do AP mode or injection. **Auto-detects** the adapter via `lsusb` (or prompts / takes an explicit `ach`|`axml` arg) and installs the right driver:
- **AWUS036ACH** → Realtek **RTL8812AU** → out-of-tree aircrack-ng DKMS module (survives kernel updates).
- **AWUS036AXML** → MediaTek **MT7921AU** → in-kernel `mt7921u` (mainline ≥5.12) + `linux-firmware` blobs (no DKMS).
Point `[offensive] ap_interface = wlan1` (and the capture interface) at it. Run after `install.sh`: `sudo bash setup/install_alfa.sh [ach|axml]`.

## Monitor mode plan

> **RESOLVED (2026-06-10): Stages 1 & 2 are dead ends — go straight to Stage 3 (USB adapter).** This board's radio is a **Synaptics 43436s**, which nexmon does not support; the nexmon DKMS build actually broke `wlan0` (probe error -110) and was removed. Internal monitor mode is not achievable. The stages below are kept for history. Anything that transmits/captures (handshake capture, deauth, rogue AP) needs an external adapter.

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

2. **Do NOT build nexmon / test internal monitor mode** — resolved as impossible on this board's Synaptics 43436s (see Monitor mode plan). Skip `install_monitor.sh`.

3. **Reboot** — activates USB gadget, SPI, I2C

4. **Reconnect via USB** at `ubuntu@10.55.0.1`

5. **Attach a USB WiFi adapter** (e.g. Alfa AWUS036ACH / rtl8812au) — required for capture, deauth, and rogue AP. Set its iface in `radioman.conf` (`[capture]`/`[offensive] ap_interface`). Internal radio stays on managed-mode scanning.

6. **Edit config** — `/opt/radioman/radioman.conf` (XPLT token, bettercap credentials, display model; `[offensive] enabled` stays `false` unless running an authorized test)

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
