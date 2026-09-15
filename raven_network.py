"""Jednotná aplikační síťová politika Ravenu.

Lokální služby na loopbacku zůstávají dostupné i offline. Vzdálené adresy
jsou v offline nebo bezpečném režimu odmítnuty ještě před otevřením spojení.
"""

from __future__ import annotations

import ipaddress
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


def load_network_settings(root: Path) -> dict[str, Any]:
    path = Path(root).resolve() / "runtime" / "raven-1.2-settings.json"
    if not path.is_file():
        return {"offline_mode": False, "safe_mode": False}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        # Poškozené bezpečnostní nastavení nesmí nepozorovaně povolit internet.
        return {"offline_mode": True, "safe_mode": True, "settings_error": True}
    return value if isinstance(value, dict) else {"offline_mode": True, "safe_mode": True, "settings_error": True}


def offline_enabled(settings: dict[str, Any]) -> bool:
    return settings.get("offline_mode") is True or settings.get("safe_mode") is True


def is_loopback_url(value: str) -> bool:
    try:
        parsed = urlsplit(str(value).strip())
        host = parsed.hostname
        if parsed.scheme not in {"http", "https"} or not host:
            return False
        if host.lower() == "localhost":
            return True
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        return False


def require_network_url(value: str, settings: dict[str, Any], purpose: str = "síťová operace") -> str:
    url = str(value or "").strip()
    if is_loopback_url(url):
        return url
    if offline_enabled(settings):
        raise ValueError(f"{purpose.capitalize()} je v offline nebo bezpečném režimu zablokovaná.")
    return url


NETWORK_COMMAND = re.compile(
    r"(?i)(?:^|[\s;|&])(curl(?:\.exe)?|wget(?:\.exe)?|ssh|scp|sftp|ftp|telnet|ping|tracert|nslookup|winget|choco)(?=\s|$)|"
    r"\b(invoke-webrequest|invoke-restmethod|start-bitstransfer|system\.net|webclient|httpclient|"
    r"git\s+(?:clone|fetch|pull|push)|ollama\s+pull|pip\s+install|npm\s+(?:install|update)|npx\s)\b"
)
URL_IN_TEXT = re.compile(r"https?://[^\s'\"`]+", re.IGNORECASE)

# Raven may inspect connectivity, but it must never silently reconfigure the
# host network. These commands can drop DHCP, replace DNS/gateway settings,
# reset an adapter or enable a VPN/proxy and affect every program on the PC.
NETWORK_RECONFIGURATION = re.compile(
    r"(?ix)"
    r"\bipconfig(?:\.exe)?\s+/(?:release|renew|flushdns|registerdns)\b|"
    r"\bnetsh(?:\.exe)?\s+(?:interface|int|winsock|winhttp)\b.*\b(?:set|reset|add|delete)\b|"
    r"\broute(?:\.exe)?\s+(?:add|change|delete)\b|"
    r"\brasdial(?:\.exe)?\b|"
    r"\b(?:set|new|remove)-net(?:ipaddress|ipinterface|route|adapter|adapterbinding)\b|"
    r"\b(?:restart|disable|enable|rename|reset)-netadapter\b|"
    r"\bset-dnsclient(?:serveraddress)?\b|"
    r"\b(?:add|remove)-vpnconnection\b|"
    r"\bset-itemproperty\b[^\r\n;|&]*(?:internet settings|proxyenable|proxyserver)"
)


def require_command_network(command: str, settings: dict[str, Any]) -> str:
    text = str(command or "")
    if not offline_enabled(settings) or not NETWORK_COMMAND.search(text):
        return text
    urls = URL_IN_TEXT.findall(text)
    if urls and all(is_loopback_url(url.rstrip("),.;")) for url in urls):
        return text
    raise ValueError("Síťový příkaz je v offline nebo bezpečném režimu zablokovaný.")


def prohibit_network_reconfiguration(command: str) -> str:
    """Reject commands that could change host-wide IP, DNS, proxy or adapters."""
    text = str(command or "")
    if NETWORK_RECONFIGURATION.search(text):
        raise ValueError(
            "Raven nesmí měnit IP, DNS, proxy, VPN ani stav síťového adaptéru. "
            "Síť může pouze sledovat; systémové změny proveďte ručně ve Windows."
        )
    return text
