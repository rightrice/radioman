"""
fingerprint.py — coarse device-type classification.

Vendor (OUI) lookup itself lives in scanner.py (_load_oui, fed by nmap's
mac-prefixes DB). This module turns an already-resolved vendor string — plus
the SSID and AP/client context — into a coarse `device_type` used for dashboard
icons and to give the AI better-grounded data.

It's a best-effort HINT, not device identity: OUI tells you the maker, not the
product, and many makers (Apple, Samsung, Google, Amazon) span categories.
Returns one of:
  router phone computer tablet iot tv printer camera voip wearable gaming
  sbc unknown
"""

import logging

log = logging.getLogger("fingerprint")

# Ordered vendor-substring rules: (device_type, [needles]). First match wins, so
# specific product makers come before generic NIC/silicon makers. All lowercase.
_VENDOR_RULES = [
    ("printer",  ["hewlett", "hp inc", "canon", "epson", "brother", "lexmark",
                  "xerox", "ricoh", "kyocera", "zebra tech"]),
    ("camera",   ["hikvision", "dahua", "reolink", "axis communication",
                  "amcrest", "foscam", "arlo", "lorex", "hangzhou"]),
    ("voip",     ["polycom", "yealink", "grandstream", "avaya", "snom", "mitel"]),
    ("wearable", ["fitbit", "garmin", "whoop", "oura"]),
    ("sbc",      ["raspberry", "arduino", "espressif", "particle", "beaglebone",
                  "seeed", "adafruit", "onion corp", "sparkfun"]),
    ("iot",      ["tuya", "sonoff", "itead", "shelly", "broadlink", "wemo",
                  "belkin", "lifx", "signify", "philips lighting", "ring",
                  "nest labs", "ecobee", "wyze", "sengled", "meross", "govee",
                  "amazon", "google", "sonos", "ecovacs", "irobot", "tp-link kasa"]),
    ("gaming",   ["nintendo", "sony interactive", "valve corp", "microsoft" ]),
    ("tv",       ["roku", "vizio", "tcl ", "hisense", "lg electronics",
                  "chromecast", "nvidia", "skyworth", "sceptre"]),
    ("router",   ["ubiquiti", "eero", "netgear", "tp-link", "tplink", "asustek",
                  "arris", "sagemcom", "cisco", "aruba", "ruckus", "mikrotik",
                  "d-link", "dlink", "linksys", "zyxel", "technicolor",
                  "actiontec", "calix", "adtran", "juniper", "fortinet",
                  "meraki", "commscope", "cradlepoint", "extreme networks",
                  "sercomm", "askey", "zte", "huawei technolog"]),
    ("phone",    ["apple", "samsung electro", "xiaomi", "oneplus", "motorola",
                  "oppo", "vivo mobile", "nokia", "huawei device", "honor device"]),
    ("computer", ["intel corp", "realtek", "liteon", "lite-on", "azurewave",
                  "murata", "quanta", "wistron", "foxconn", "hon hai", "pegatron",
                  "compal", "dell", "lenovo", "vmware", "giga-byte", "micro-star",
                  "framework", "clevo"]),
]

# High-confidence SSID-name hints (mostly useful for an AP's own SSID, e.g. a
# printer/cast SoftAP). All lowercase substrings.
_SSID_RULES = [
    ("printer",  ["hp-print", "hp-setup", "[printer]", "canon_", "epson",
                  "directhp", "brother"]),
    ("tv",       ["chromecast", "roku", "[tv]", "samsung tv", "lg webos",
                  "[lg]", "vizio", "bravia", "firetv", "fire tv"]),
    ("iot",      ["echo", "amazon-", "ring-", "nest-", "wyze", "shelly",
                  "tplink_smart", "kasa", "govee", "sonos"]),
    ("phone",    ["iphone", "galaxy", "pixel"]),
    ("computer", ["macbook", "desktop", "-pc", "laptop"]),
    ("camera",   ["ipcam", "ip-cam", "reolink", "hikvision", "dahua"]),
]


def _is_local_mac(mac: str) -> bool:
    """True if the MAC's locally-administered bit is set — i.e. a randomized
    (privacy) address, as modern phones use when probing/associating."""
    try:
        first = int(mac.replace("-", ":").split(":")[0], 16)
        return bool(first & 0x02)
    except (ValueError, IndexError, AttributeError):
        return False


def device_type_for(mac: str = "", vendor: str = "", ssid: str = "",
                    is_ap: bool = False) -> str:
    """Best-effort device class. Order: SSID hints → vendor rules → randomized-MAC
    → AP context → unknown."""
    s = (ssid or "").lower()
    for dtype, needles in _SSID_RULES:
        if any(n in s for n in needles):
            return dtype

    v = (vendor or "").lower()
    if v and v not in ("unknown", "?"):
        for dtype, needles in _VENDOR_RULES:
            if any(n in v for n in needles):
                return dtype

    # No usable vendor: a locally-administered MAC is almost always a phone
    # using a randomized address.
    if not v and _is_local_mac(mac):
        return "phone"

    # An access point we couldn't otherwise place is most likely network gear.
    if is_ap:
        return "router"
    return "unknown"
