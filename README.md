# radioman 📡

A tamagotchi-style Wi-Fi audit console for the Raspberry Pi Zero 2W.
Combines the spirit of Pwnagotchi, Ubiquiti WiFiman, and the Aircrack-ng suite.

## Hardware

| Component | Details |
|---|---|
| SBC | Raspberry Pi Zero 2W |
| Battery | PiSugar 2 |
| Display | Waveshare 2.13" e-ink (250×122) |
| Radio | Internal CYW43439 (monitor mode) |
| Adapter *(future)* | Alfa AWUS036ACH via micro-USB OTG |

## Features

- **Passive Wi-Fi scanning** via bettercap — discovers APs and associated clients
- **Handshake capture** — PMKID and EAPOL, saved as `.pcap` files
- **Auto-crack queue** — aircrack-ng + rockyou wordlist, runs automatically on each capture
- **Network graph** — airgraph-ng-style AP↔client relationship canvas
- **LAN host discovery** — passive ARP + on-demand nmap scan
- **Tamagotchi personality** — mood engine with ASCII faces on the e-ink display
- **PiSugar 2 battery** — heart-strip display, charging indicator
- **Web dashboard** — aspect2020 design system, dark/light theme, auto-refresh

## Quick Start

### 1. Flash Pi OS

Download **Raspberry Pi OS Lite 64-bit (Bookworm)** and flash to SD card.
Enable SSH in the Raspberry Pi Imager advanced settings.

### 2. Install

```bash
git clone https://github.com/rightrice/radioman
cd radioman
sudo bash setup/install.sh
sudo reboot
```

### 3. Access

```
Web dashboard:  http://<pi-ip>:8080
Logs:           journalctl -u radioman -f
Captures:       /opt/radioman/captures/
```

## Project Structure

```
radioman/
├── config/
│   └── radioman.conf       # Main configuration
├── daemon/
│   ├── radioman.py         # Main orchestrator
│   ├── api.py              # Flask REST API + static serving
│   ├── capture.py          # bettercap integration
│   ├── cracker.py          # aircrack-ng queue
│   ├── scanner.py          # Network discovery (ARP + nmap)
│   ├── personality.py      # Mood engine + ASCII faces
│   ├── display.py          # Waveshare e-ink driver
│   ├── battery.py          # PiSugar 2 I2C integration
│   └── db.py               # SQLite database
├── setup/
│   ├── install.sh          # Full install script
│   ├── radioman.service    # systemd service
│   └── radioman.cap        # bettercap caplet
└── web/
    ├── index.html
    └── assets/
        ├── css/
        │   ├── sandbox.css     # aspect2020 design system
        │   └── radioman.css    # Dashboard styles
        └── js/
            └── dashboard.js    # Dashboard logic
```

## Configuration

Edit `/opt/radioman/radioman.conf` (or `config/radioman.conf` before install):

```ini
[radioman]
interface = wlan0       # Monitor mode interface
web_port = 8080

[capture]
bettercap_user = user
bettercap_pass = pass   # Change before deploying

[cracker]
wordlist = /opt/radioman/wordlists/rockyou.txt

[display]
model = epd2in13_V3     # Match your Waveshare version
rotate = 180
```

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/status` | Personality, battery, stats |
| GET | `/api/networks` | All discovered APs |
| GET | `/api/clients` | All discovered clients |
| GET | `/api/captures` | Handshake captures |
| POST | `/api/crack/<id>` | Trigger crack for capture |
| GET | `/api/graph` | Graph nodes + edges |
| GET | `/api/hosts` | LAN hosts (ARP) |
| POST | `/api/hosts/scan` | Run nmap scan |
| GET | `/api/events` | Event log |
| POST | `/api/cmd` | Send bettercap command |

## Legal

For use on networks you own or have explicit written permission to test.
The authors are not responsible for misuse.

---

*Built with bettercap, aircrack-ng, Flask, and the aspect2020 design system.*