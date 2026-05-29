# radioman

A tamagotchi-style Wi-Fi audit console for the Raspberry Pi Zero 2W.
Combines the spirit of Pwnagotchi, Ubiquiti WiFiman, and the Aircrack-ng suite.

## Hardware

| Component | Details |
|---|---|
| SBC | Raspberry Pi Zero 2W |
| Battery | PiSugar 2 |
| Display | Waveshare 2.13" e-ink (250×122) |
| Radio | Internal BCM43430A1 (CYW43438) — monitor mode via nexmon DKMS |

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

---

## Install

### 1. Flash Kali Linux

<<<<<<< Updated upstream
Download the **Kali Linux Raspberry Pi ARM** image from [kali.org/get-kali](https://www.kali.org/get-kali/#kali-arm).
Use the **64-bit** image for the Pi Zero 2W.

Flash to SD card with Raspberry Pi Imager or Balena Etcher.

> **First boot credentials:** `kali` / `kali`
> Change the password immediately: `passwd`

### 2. First boot

Connect the Pi to your home WiFi or plug in the USB cable (see section 4 first).
Then SSH in:
=======
Download the **Kali Linux arm64** image for the Raspberry Pi Zero 2W from [kali.org/get-kali/#kali-arm](https://www.kali.org/get-kali/#kali-arm).

Flash it to SD card using Raspberry Pi Imager (choose "Use custom" and select the `.img.xz`) or Balena Etcher.

Default credentials on first boot: user `kali`, password `kali`.

### 2. First boot

SSH in over WiFi (default hostname is `kali`):
```bash
ssh kali@kali.local
```

Change the default password immediately:
```bash
passwd
```

Set the hostname to `radioman`:
```bash
sudo hostnamectl set-hostname radioman
sudo sed -i 's/kali/radioman/g' /etc/hosts
```
>>>>>>> Stashed changes

```bash
ssh kali@radioman.local
# or by IP if mDNS isn't working yet:
ssh kali@<pi-ip>
```

### 3. Clone and install

```bash
git clone https://github.com/rightrice/radioman
cd radioman
sudo bash setup/install.sh
sudo reboot
```

<<<<<<< Updated upstream
`install.sh` handles:
- Sets hostname to `radioman`
- Swap + zram (memory management for 512MB RAM)
- nexmon DKMS install (monitor mode — no firmware-nexmon)
- Security tools verification (Kali pre-installs most)
- SPI, I2C, USB gadget ethernet
- Python venv, Waveshare library, radioman service

> **Note:** `brcmfmac-nexmon-dkms` patches the brcmfmac kernel driver to allow
> monitor mode with the stock Cypress firmware. `firmware-nexmon` is explicitly
> **not** installed — it replaces Cypress firmware files and crashes the
> BCM43430A1 (chip revision mismatch).

### 4. USB gadget ethernet (recommended)

After reboot, connect the Pi to your Mac via USB **data** cable (not the PWR port) and run on your Mac:
=======
`install.sh` configures swap, zram, SPI, I2C, USB gadget ethernet, bettercap, aircrack-ng, hcxtools, hashcat, and the radioman systemd service. Tools already present on Kali are detected and skipped.

### 4. USB gadget ethernet (recommended)

Connect the Pi to your machine via USB **data** cable (not the PWR port after reboot), then run the connect script for your OS.

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

Windows tried DHCP and got a link-local address. The script handles this automatically by assigning `10.55.0.2` statically. If you need to do it manually:

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

The Pi will get a `192.168.137.x` address from Windows. SSH at `10.55.0.1` continues to work (static IP from `install.sh` is restored by the script). To return to static-only mode:

```bash
sudo nmcli connection modify usb-gadget ipv4.method manual ipv4.addresses 10.55.0.1/24
sudo nmcli connection up usb-gadget
```

**The ICS rule does not survive reboot** — re-run `win_connect.ps1 -Share` each time.

---

#### macOS
>>>>>>> Stashed changes

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

<<<<<<< Updated upstream
This happens because macOS tries DHCP and the Pi doesn't run a DHCP server on `usb0`. The setting persists across reboots once saved.

**If the USB gadget disappears after every reboot:**

The `g_ether` kernel module randomises its MAC by default — macOS sees a new device each boot. `install.sh` sets a persistent MAC via `/etc/modprobe.d/g_ether.conf`. If it happens anyway, run on the Pi:

```bash
echo "options g_ether host_addr=72:48:4f:52:4d:01 dev_addr=72:48:4f:52:4d:02" | sudo tee /etc/modprobe.d/g_ether.conf
sudo rmmod g_ether && sudo modprobe g_ether
```

### 5. Internet sharing (Pi needs internet for git pull, apt, etc.)

Run on your Mac each time you need the Pi to have internet access:
=======
**Internet sharing — macOS:**
>>>>>>> Stashed changes

```bash
bash scripts/mac_connect.sh share
```

This enables NAT via pfctl so the Pi routes through your Mac's WiFi. **The rule does not survive Mac sleep or reboot** — re-run it whenever you lose Pi internet.

<<<<<<< Updated upstream
`install.sh` already configures the usb-gadget NM profile with gateway `10.55.0.2` and DNS `1.1.1.1` — no extra Pi-side steps needed.

**Verify:**
```bash
# On Pi
ping -c 2 1.1.1.1       # IP routing works
ping -c 2 github.com    # DNS works
```

### 6. Configure
=======
One-time Pi-side setup (do this once after `install.sh`):

```bash
sudo nmcli connection modify usb-gadget \
  ipv4.gateway 10.55.0.2 \
  ipv4.never-default no \
  ipv4.dns "1.1.1.1 8.8.8.8"
sudo nmcli connection up usb-gadget
```

### 5. Configure
>>>>>>> Stashed changes

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

### 7. Monitor mode

Monitor mode is enabled by the `brcmfmac-nexmon-dkms` package installed in step 3.
It creates a virtual `mon0` interface alongside `wlan0` so WiFi connectivity is
maintained while scanning.

<<<<<<< Updated upstream
If you need to verify or repair monitor mode manually:
=======
Kali's Pi Zero 2W image ships standard Raspberry Pi firmware. VDEV monitor mode works the same way as on Pi OS.

If you have nexmon packages installed and monitor mode is failing (packages `brcmfmac-nexmon-dkms` or `firmware-nexmon`), clean up with:
>>>>>>> Stashed changes

```bash
sudo bash setup/install_monitor.sh
```

This installs the DKMS package if missing, reloads the driver, and tests that
`mon0` can be created. Safe to run multiple times.

<<<<<<< Updated upstream
> **Important:** `wlan0` disconnects from WiFi while bettercap is scanning on `mon0`.
> Use the USB cable (10.55.0.1) as your primary management connection.
=======
> **Kali tool note:** aircrack-ng, hcxtools, hashcat, bettercap, and nmap are typically pre-installed in Kali. `install.sh` detects existing installations and skips re-installing them.

---
>>>>>>> Stashed changes

### 8. Install AI (optional)

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

Downloads IBM Granite 4.0 350M Q4_K_M (~230MB). Inference takes ~60s per response on the Pi Zero 2W.

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
| USB SSH | `ssh kali@10.55.0.1` |
| WiFi SSH | `ssh kali@radioman.local` (only when not scanning) |
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
<<<<<<< Updated upstream
│   ├── mac_connect.sh          # Mac-side USB gadget + internet sharing setup
=======
│   ├── win_connect.ps1         # Windows 11 USB gadget setup (PowerShell, run as Admin)
│   ├── mac_connect.sh          # macOS USB gadget setup
>>>>>>> Stashed changes
│   └── build_llama_wsl.sh      # Cross-compile llama-cli (WSL2)
├── setup/
│   ├── install.sh              # Full install script (Kali Linux)
│   ├── install_monitor.sh      # Install / verify nexmon DKMS monitor mode
│   ├── install_ai.sh           # AI model + binary installer
│   ├── install_pisugar.sh      # PiSugar 2 battery setup
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

---

## Legal

For use on networks you own or have explicit written permission to test.
The authors are not responsible for misuse.

---

*Built with bettercap, aircrack-ng, llama.cpp, Flask, and the aspect2020 design system.*
