# radioman

A tamagotchi-style Wi-Fi audit console for the Raspberry Pi Zero 2W.
Combines the spirit of Pwnagotchi, Ubiquiti WiFiman, and the Aircrack-ng suite.

## Hardware

| Component | Details |
|---|---|
| SBC | Raspberry Pi Zero 2W |
| Battery | PiSugar 2 |
| Display | Waveshare 2.13" e-ink (250×122) |
| Radio | Internal CYW43439 (monitor mode) |

## Features

- **Passive Wi-Fi scanning** via bettercap — discovers APs and associated clients
- **Handshake capture** — PMKID and EAPOL, saved as `.pcapng` files with dashboard download
- **Auto-crack queue** — hashcat (PMKID) + aircrack-ng (EAPOL) with rockyou wordlist
- **Network graph** — AP↔client relationship canvas
- **LAN host discovery** — passive ARP + on-demand nmap scan
- **Local AI assistant** — IBM Granite 4.0 350M running via llama.cpp, chat + network analysis
- **XPLT cloud sync** — pushes networks, clients, and captures to the XPLT platform
- **Tamagotchi personality** — mood engine with ASCII faces on the e-ink display
- **PiSugar 2 battery** — heart-strip display, charging indicator via direct I2C
- **Web dashboard** — dark/light theme, auto-refresh, capture downloads

---

## Install

### 1. Flash Pi OS

Flash **Raspberry Pi OS Lite 64-bit (Bookworm)** to SD card using Raspberry Pi Imager.
In Imager advanced settings: enable SSH, set hostname to `radioman`, set username `pi`.

### 2. First boot

SSH in over WiFi:
```bash
ssh pi@radioman.local
```

> **Note:** radioman creates a virtual monitor interface (`mon0`) alongside `wlan0` during scanning — `wlan0` is disconnected from WiFi while scanning is active.
> Use the USB cable (10.55.0.1) as your primary management connection after setup.

### 3. Clone and install

```bash
git clone https://github.com/rightrice/radioman
cd radioman
sudo bash setup/install.sh
sudo reboot
```

`install.sh` configures swap, zram, SPI, I2C, USB gadget ethernet, bettercap, aircrack-ng, hcxtools, hashcat, and the radioman systemd service.

### 4. USB gadget ethernet (recommended)

After reboot, connect the Pi to your Mac via USB **data** cable (not the PWR port) and run:

```bash
bash scripts/mac_connect.sh
```

This sets your Mac USB interface to `10.55.0.2` and verifies the Pi is reachable at `10.55.0.1`.

SSH over USB:
```bash
ssh pi@10.55.0.1
```

**If the USB interface shows "Self-assigned IP" or `169.254.x.x` in System Settings:**

macOS found the gadget but assigned a DHCP link-local address. Fix it manually:

1. Open **System Settings → Network**
2. Find **RNDIS/Ethernet Gadget** (or Raspberry Pi USB Gadget)
3. Click **Details → TCP/IP**
4. Set Configure IPv4: **Manually**, IP: `10.55.0.2`, Mask: `255.255.255.0`

This happens because macOS tries DHCP and the Pi doesn't run a DHCP server on `usb0`. The setting persists across reboots once saved.

**If the USB gadget disappears after every reboot:**

The `g_ether` kernel module randomises its MAC by default — macOS sees a new device each boot. `install.sh` sets a persistent MAC via `/etc/modprobe.d/g_ether.conf`, so this should not happen on a fresh install. If it does, run on the Pi:

```bash
echo "options g_ether host_addr=72:48:4f:52:4d:01 dev_addr=72:48:4f:52:4d:02" | sudo tee /etc/modprobe.d/g_ether.conf
sudo rmmod g_ether && sudo modprobe g_ether
```

**Internet sharing (Pi needs internet for git pull, apt, etc.):**

Run on your Mac each time you need the Pi to have internet access:

```bash
bash scripts/mac_connect.sh share
```

This enables NAT via pfctl so the Pi routes through your Mac's WiFi. **The rule does not survive Mac sleep or reboot** — re-run it whenever you lose Pi internet.

One-time Pi-side setup (do this once after `install.sh`):

```bash
# Add default gateway and DNS to the usb-gadget NM profile
sudo nmcli connection modify usb-gadget \
  ipv4.gateway 10.55.0.2 \
  ipv4.never-default no \
  ipv4.dns "1.1.1.1 8.8.8.8"
sudo nmcli connection up usb-gadget
```

After this the Pi knows to route through the Mac and resolve DNS — you only need to re-run `mac_connect.sh share` on the Mac side after each sleep/reboot.

**Verify:**
```bash
# On Pi
ping -c 2 1.1.1.1       # IP routing works
ping -c 2 github.com    # DNS works
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

The Pi Zero 2W's BCM43430A1 chip supports VDEV monitor mode with its stock Cypress firmware — no nexmon patching required. radioman creates `mon0` automatically when scanning starts.

If you previously installed nexmon (packages `brcmfmac-nexmon-dkms` or `firmware-nexmon`), clean up with:

```bash
sudo bash setup/install_monitor.sh
sudo reboot
```

This removes nexmon packages, restores the original Cypress firmware, and verifies VDEV monitor mode works.

---

### 7. Install AI (optional)

The AI assistant requires a pre-built `llama-cli` binary (cross-compile from a more powerful machine) and the Granite model download.

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
| USB SSH | `ssh pi@10.55.0.1` |
| WiFi SSH | `ssh pi@radioman.local` (only when not scanning) |
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
│   ├── personality.py          # Mood engine + ASCII faces
│   ├── display.py              # Waveshare e-ink driver
│   ├── battery.py              # PiSugar 2 I2C integration
│   ├── db.py                   # SQLite database
│   └── xplt.py                 # XPLT cloud sync
├── scripts/
│   ├── mac_connect.sh          # Mac-side USB gadget setup
│   └── build_llama_wsl.sh      # Cross-compile llama-cli (WSL2)
├── setup/
│   ├── install.sh              # Full install script
│   ├── install_ai.sh           # AI model + binary installer
│   ├── install_monitor.sh      # Clean up nexmon; verify VDEV monitor mode
│   ├── update.sh               # Update deployed files
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
