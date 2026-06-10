# radioman

A tamagotchi-style Wi-Fi audit console for the Raspberry Pi Zero 2W.
Combines the spirit of Pwnagotchi, Ubiquiti WiFiman, and the Aircrack-ng suite.

> **Recommended OS:** Ubuntu Server 24.04 LTS (arm64). Kali Linux is also supported but has stability issues on the Pi Zero 2W's 512MB RAM.

---

## ⚠️ Authorized & educational use only

radioman includes **active** capabilities (e.g. targeted deauthentication) intended **solely** for:

- penetration tests of networks you **own** or have **explicit written authorization** to assess, or
- education and research in an **isolated lab** you control.

**Using these features against networks or devices without authorization is illegal** in most jurisdictions (e.g. the US Computer Fraud and Abuse Act, the UK Computer Misuse Act, and equivalents) and can disrupt people who never consented. **You are solely responsible** for operating within the law and the scope of your engagement. The authors accept no liability for misuse.

Because Wi-Fi attacks affect third parties in radio range, radioman is built so active actions **only fire against targets you explicitly place in scope**, and **every attempt is logged** to an audit trail. These controls are intentional — keep them. This notice is a statement of intended use; it is **not** a substitute for those controls.

## Hardware

| Component | Details |
|---|---|
| SBC | Raspberry Pi Zero 2W (512MB) **or** Raspberry Pi 5 (4–16GB) — auto-detected |
| Battery | PiSugar 2 (optional) |
| Display | Waveshare 2.13" e-ink (250×122) |
| Radio | Internal radio can't do monitor mode on either board — capture/deauth/rogue-AP need a USB adapter (Alfa AWUS036ACH / AWUS036AXML, see `setup/install_alfa.sh`) |
| AI | Local LLM, on-device (no internet). CPU path = IBM Granite via llama.cpp (auto-scales by board). **AI HAT+ 2 (Hailo-10H)** path = Llama 3.2 1B on the NPU (~10-25× faster) — set up by `setup/install_hailo.sh`. The original AI HAT+ (Hailo-8) is vision-only and not used for the LLM. |

> radioman adapts to the board at install/runtime: the Pi 5 runs a larger, faster AI and skips the Zero 2W's swap + USB-gadget setup.

## Features

- **Passive Wi-Fi scanning** via bettercap — discovers APs and associated clients
- **Handshake capture** — PMKID and EAPOL, saved as `.pcapng` files with dashboard download
- **Auto-crack queue** — hashcat (PMKID) + aircrack-ng (EAPOL) with rockyou wordlist
- **Network graph** — AP↔client relationship canvas
- **LAN host discovery** — passive ARP + on-demand nmap scan
- **Local AI assistant** — IBM Granite 4.0 350M running via llama.cpp, chat + network analysis
- **XPLT cloud sync** — pushes networks, clients, and captures to the XPLT platform
- **Tamagotchi personality** — mood engine reflected on the e-ink display
- **PiSugar 2 battery** — heart-strip display, charging indicator via direct I2C
- **Web dashboard** — dark/light theme, auto-refresh, capture downloads
- **Active testing** *(authorized only, off by default)* — scoped targeted deauth + rogue-AP/evil-twin, gated by a Rules-of-Engagement allowlist and an audit trail

---

## Active testing (authorized engagements)

Off by default. Enable on a device you're authorized to test by setting `[offensive] enabled = true` in `radioman.conf` and restarting. Then, in the dashboard **Active** tab:

1. **Add scope.** Authorize targets in the Rules-of-Engagement panel — a BSSID, a whole network by **SSID**, an **IP/CIDR**, or paste your RoE list to bulk-import. Every entry needs an authorization reference. Nothing is actionable until it's in scope.
2. **Deauth.** The "Authorized targets — live" table shows in-scope APs in range; one tap sends a single-target, rate-limited deauth to force a handshake (needs an AP-capable monitor interface — a USB adapter on the Pi Zero).
3. **Rogue AP.** Pick an in-scope SSID, **Arm** it (attesting authorization), then **Start**. The captive portal defaults to a benign association test; credential capture is a separate, clearly-marked opt-in. Needs a second AP-capable interface (`[offensive] ap_interface`) plus `hostapd` + `dnsmasq`.

Every allow/deny decision and every action is written to the **Audit Trail** in the same tab. These controls are deliberate — see the [authorized-use notice](#️-authorized--educational-use-only).

---

## Install

### 1. Flash Ubuntu Server 24.04 LTS

Download the **Ubuntu Server 24.04 LTS Raspberry Pi** image from [ubuntu.com/download/raspberry-pi](https://ubuntu.com/download/raspberry-pi).
Use the **64-bit (arm64)** image.

Flash to SD card with Raspberry Pi Imager (choose "Use custom") or Balena Etcher.

> **First boot credentials:** `ubuntu` / `ubuntu` — you'll be forced to change the password on first login.

### 2. First boot

Connect the Pi to your home WiFi, then SSH in (default hostname is `ubuntu`):

```bash
ssh ubuntu@ubuntu.local
# or by IP if mDNS isn't working yet:
ssh ubuntu@<pi-ip>
```

> `install.sh` (next step) sets the hostname to `radioman` automatically.
> After reboot you'll use `ubuntu@radioman.local`.

### 3. Clone and install

```bash
git clone https://github.com/rightrice/radioman
cd radioman
sudo bash setup/install.sh
sudo reboot
```

`install.sh` handles:
- Sets hostname to `radioman`
- Swap + zram (memory management for 512MB RAM)
- nexmon DKMS install (monitor mode — see note below)
- Security tools verification (Kali pre-installs most)
- SPI, I2C, USB gadget ethernet
- Python venv, Waveshare library, radioman service

> **nexmon note:** `brcmfmac-nexmon-dkms` patches the brcmfmac kernel driver to allow
> monitor mode with the stock Cypress firmware. `firmware-nexmon` is explicitly
> **not** installed — it replaces Cypress firmware files and crashes the
> BCM43430A1 (chip revision mismatch).

### 4. USB gadget ethernet (recommended)

Connect the Pi to your machine via USB **data** cable (not the PWR port), then run the connect script for your OS.

#### Windows 11

```powershell
# Run as Administrator in PowerShell from the repo root:
powershell -ExecutionPolicy Bypass -File scripts\win_connect.ps1
```

This detects the RNDIS USB gadget adapter, assigns `10.55.0.2` to it, removes any routing pollution, and verifies the Pi is reachable at `10.55.0.1`.

SSH over USB:
```powershell
ssh kali@10.55.0.1
```

**If the USB adapter shows `169.254.x.x` (self-assigned):**

Windows tried DHCP and got a link-local address. The script handles this automatically. If you need to do it manually:

1. Open **Settings → Network & Internet → Advanced network settings**
2. Find the adapter labelled **Remote NDIS Compatible Device** (or similar)
3. Click **Edit** → **Manual** → set IPv4 to `10.55.0.2`, mask `255.255.255.0`

**If the USB gadget disappears after every reboot:**

The `g_ether` module randomises its MAC by default — Windows sees a new device each boot. `install.sh` sets a persistent MAC via `/etc/modprobe.d/g_ether.conf`. If it recurs, run on the Pi:

```bash
echo "options g_ether host_addr=72:48:4f:52:4d:01 dev_addr=72:48:4f:52:4d:02" | sudo tee /etc/modprobe.d/g_ether.conf
sudo rmmod g_ether && sudo modprobe g_ether
```

**Internet sharing — Windows (Pi needs internet for git pull, apt, etc.):**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\win_connect.ps1 -Share
```

This enables Windows ICS from your WiFi to the USB adapter. ICS changes the adapter to `192.168.137.1` — the script re-adds `10.55.0.2` so SSH still works, but for the Pi to actually use the internet it needs to be in DHCP mode on `usb0`:

```bash
# On the Pi — switch usb0 to DHCP for internet access
sudo nmcli connection modify usb-gadget ipv4.method auto
sudo nmcli connection up usb-gadget
```

The Pi will get a `192.168.137.x` address from Windows ICS. SSH at `10.55.0.1` continues to work (static IP is preserved by the script). To return to static-only mode:

```bash
sudo nmcli connection modify usb-gadget ipv4.method manual ipv4.addresses 10.55.0.1/24
sudo nmcli connection up usb-gadget
```

**The ICS rule does not survive reboot** — re-run `win_connect.ps1 -Share` each time.

---

#### macOS

```bash
bash scripts/mac_connect.sh
```

This sets your Mac USB interface to `10.55.0.2` and verifies the Pi is reachable at `10.55.0.1`.

SSH over USB:
```bash
ssh kali@10.55.0.1
```

**If the USB interface shows "Self-assigned IP" or `169.254.x.x` in System Settings:**

macOS tried DHCP and got a link-local address. Fix it manually:

1. Open **System Settings → Network**
2. Find **RNDIS/Ethernet Gadget** (or Raspberry Pi USB Gadget)
3. Click **Details → TCP/IP**
4. Set Configure IPv4: **Manually**, IP: `10.55.0.2`, Mask: `255.255.255.0`

This happens because macOS tries DHCP and the Pi doesn't run a DHCP server on `usb0`. The setting persists across reboots once saved.

**If the USB gadget disappears after every reboot:**

The `g_ether` kernel module randomises its MAC by default — macOS sees a new device each boot. `install.sh` sets a persistent MAC via `/etc/modprobe.d/g_ether.conf`. If it happens anyway, run on the Pi:

```bash
echo "options g_ether host_addr=72:48:4f:52:4d:01 dev_addr=72:48:4f:52:4d:02" | sudo tee /etc/modprobe.d/g_ether.conf
sudo rmmod g_ether && sudo modprobe g_ether
```

**Internet sharing — macOS:**

```bash
bash scripts/mac_connect.sh share
```

This enables NAT via pfctl so the Pi routes through your Mac's WiFi. `install.sh` already configures the `usb-gadget` NM profile with gateway `10.55.0.2` and DNS `1.1.1.1` — no extra Pi-side steps needed.

**Verify:**
```bash
# On Pi
ping -c 2 1.1.1.1       # IP routing works
ping -c 2 github.com    # DNS works
```

**The rule does not survive Mac sleep or reboot** — re-run `mac_connect.sh share` each time.

---

#### Ubuntu / Linux

```bash
bash scripts/linux_connect.sh
```

This detects the CDC ECM USB gadget adapter, assigns `10.55.0.2` via NetworkManager (or falls back to raw `ip` commands), and verifies the Pi is reachable at `10.55.0.1`.

SSH over USB:
```bash
ssh kali@10.55.0.1
```

**If the USB interface doesn't show up:**

Check that the `cdc_ether` driver is loaded:
```bash
lsmod | grep cdc_ether
# If missing:
sudo modprobe cdc_ether
```

Then check `ip link show` or `nmcli device status` for a new ethernet interface after plugging in.

**Internet sharing — Linux:**

```bash
bash scripts/linux_connect.sh share
```

This enables iptables NAT so the Pi routes internet traffic through your upstream interface. Unlike Windows ICS, the adapter IP is not changed — the Pi stays reachable at `10.55.0.1` in both modes. No Pi-side changes needed (`install.sh` already configured the gateway).

**Verify on Pi:**
```bash
ping -c 2 1.1.1.1     # IP routing works
ping -c 2 github.com  # DNS works
```

**Rules are session-only** — re-run `linux_connect.sh share` after reboot. To stop:
```bash
sudo iptables -t nat -F POSTROUTING && sudo iptables -F FORWARD
```

### 5. Configure

Edit `/opt/radioman/radioman.conf`:

```ini
[radioman]
interface = wlan0
web_port = 8080

[capture]
bettercap_user = user
bettercap_pass = pass

[cracker]
wordlist = /opt/radioman/wordlists/rockyou.txt

[display]
model = epd2in13_V4    # match your Waveshare version
rotate = 180

[database]
rssi_history_hours = 24

[xplt]
device_token =         # set after pairing in dashboard
```

### 6. Monitor mode

Monitor mode is enabled by the `brcmfmac-nexmon-dkms` package installed in step 3.
It creates a virtual `mon0` interface alongside `wlan0` so WiFi connectivity is
maintained while scanning.

If you need to verify or repair monitor mode:

```bash
sudo bash setup/install_monitor.sh
```

This installs the DKMS package if missing, reloads the driver, and tests that `mon0` can be created. Safe to run multiple times.

> **Important:** `wlan0` disconnects from WiFi while bettercap is scanning on `mon0`.
> Use the USB cable (`10.55.0.1`) as your primary management connection.

**External adapter (for capture / deauth / rogue AP):** run `sudo bash setup/install_alfa.sh` — it auto-detects an Alfa **AWUS036ACH** (Realtek RTL8812AU) or **AWUS036AXML** (MediaTek MT7921AU), or prompts if it can't, and installs the right driver. Pass `ach` or `axml` to force the model.

---

### 7. Install AI (optional)

The AI assistant requires a pre-built `llama-cli` binary and the Granite model.

**Step 1 — Build llama-cli** (run on WSL2 Ubuntu or a Linux machine, not the Pi):
```bash
bash scripts/build_llama_wsl.sh radioman.local
```
Then on the Pi:
```bash
sudo mkdir -p /opt/radioman/llama
sudo mv /tmp/llama-cli /opt/radioman/llama/llama-cli
sudo chmod +x /opt/radioman/llama/llama-cli
```

**Step 2 — Download Granite model** (run on the Pi):
```bash
sudo bash setup/install_ai.sh
```

Board-aware: the **Pi Zero 2W** gets IBM Granite 4.0 350M (~230MB, ~60s/response); the **Pi 5** gets Granite 3.3 2B (~1.5GB, a few seconds/response). Override with `sudo MODEL_REPO=<hf/repo> MODEL_FILENAME=<file.gguf> bash setup/install_ai.sh`.

> The assistant runs entirely on-device as a local `llama-cli` subprocess — **no network calls**. (If you swap in a non-Granite model, update the prompt template in `daemon/ai.py`.)

---

## Update

After `git pull`, run:

```bash
sudo bash setup/update.sh
```

Updates daemon files, web assets, caplet, and service. Never touches `radioman.conf`, `captures/`, or `wordlists/`.

---

## Access

| Interface | Address |
|---|---|
| Web dashboard | `http://radioman.local:8080` |
| USB SSH | `ssh ubuntu@10.55.0.1` |
| WiFi SSH | `ssh ubuntu@radioman.local` (only when not scanning) |
| Logs | `journalctl -u radioman -f` |
| Captures | `/opt/radioman/captures/` |

---

## Project Structure

```
radioman/
├── config/
│   └── radioman.conf.example   # Config template
├── daemon/
│   ├── radioman.py             # Main orchestrator
│   ├── api.py                  # Flask REST API + static serving
│   ├── ai.py                   # IBM Granite AI engine (llama.cpp)
│   ├── capture.py              # bettercap integration
│   ├── cracker.py              # hashcat + aircrack-ng queue
│   ├── scanner.py              # Network discovery (ARP + nmap)
│   ├── personality.py          # Mood engine
│   ├── display.py              # Waveshare e-ink driver
│   ├── battery.py              # PiSugar 2 I2C integration
│   ├── db.py                   # SQLite database
│   └── xplt.py                 # XPLT cloud sync
├── scripts/
│   ├── win_connect.ps1         # Windows 11 USB gadget setup (PowerShell, run as Admin)
│   ├── mac_connect.sh          # macOS USB gadget + internet sharing setup
│   ├── linux_connect.sh        # Ubuntu/Linux USB gadget setup (iptables NAT sharing)
│   └── build_llama_wsl.sh      # Cross-compile llama-cli (WSL2)
├── setup/
│   ├── install.sh              # Full install script
│   ├── install_monitor.sh      # nexmon DKMS (gated; not for the Synaptics 43436s)
│   ├── fix_wlan0.sh            # Repair: remove broken nexmon driver, restore wlan0
│   ├── install_alfa.sh         # External adapter driver (AWUS036ACH / AWUS036AXML)
│   ├── install_ai.sh           # CPU AI: llama-cli + Granite model (board-aware)
│   ├── install_hailo.sh        # AI HAT+ 2 (Hailo-10H) NPU LLM — Llama 3.2 1B on-device
│   ├── install_pisugar.sh      # PiSugar 2 battery setup
│   ├── tune.sh                 # Disable unused Ubuntu services, reduce RAM/IO usage
│   ├── update.sh               # Update deployed files after git pull
│   ├── radioman.service        # systemd service
│   └── radioman.cap            # bettercap caplet
└── web/
    ├── index.html
    └── assets/
        ├── css/
        │   ├── sandbox.css     # aspect2020 design system
        │   └── radioman.css    # Dashboard styles
        └── js/
            └── dashboard.js    # Dashboard logic
```

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/status` | Personality, battery, scan stats |
| GET | `/api/networks` | All discovered APs |
| GET | `/api/clients` | All discovered clients |
| GET | `/api/captures` | Handshake captures |
| GET | `/api/captures/<id>/download` | Download pcapng file |
| POST | `/api/crack/<id>` | Queue crack for capture |
| GET | `/api/graph` | Graph nodes + edges |
| GET | `/api/hosts` | LAN hosts (ARP) |
| POST | `/api/hosts/scan` | Run nmap scan |
| GET | `/api/events` | Event log |
| POST | `/api/cmd` | Send bettercap command |
| GET | `/api/xplt/status` | XPLT sync status |
| POST | `/api/xplt/pair` | Pair with XPLT |
| POST | `/api/xplt/sync` | Trigger manual sync |
| GET | `/api/ai/status` | AI engine status |
| POST | `/api/ai/chat` | Chat with Granite |
| POST | `/api/ai/analyze` | Analyze networks or passwords |
| GET | `/api/passwords` | Offline password intelligence (strength, patterns, reuse, defaults) |
| GET | `/api/vault` | Capture-encryption status (enabled, mode, locked, key fingerprint) |
| POST | `/api/vault/unlock` | Unlock the capture vault (pin mode) with a passphrase/PIN |
| POST | `/api/vault/lock` | Clear the in-memory vault key (pin mode) |

---

## Legal

For use on networks you own or have explicit written permission to test.
The authors are not responsible for misuse.

---

*Built with bettercap, aircrack-ng, llama.cpp, Flask, and the radioman design system.*
