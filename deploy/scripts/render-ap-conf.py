#!/usr/bin/python3
"""hostapd-Konfiguration des Notfall-Hotspots aus der config.yaml erzeugen.

Bis 2026-09-28 las hostapd /etc/hostapd/ft8-ap.conf so, wie install.sh sie
aus dem Repo kopiert hatte — mit der Platzhalter-Passphrase, die im
oeffentlichen Repository steht. Die zufaellige Passphrase, die install.sh
erzeugt und die Oberflaeche anzeigt, landete nur in der config.yaml; hostapd
las sie nie. Fiel das Netz aus, startete die Station einen Hotspot, dessen
Passwort jeder nachlesen konnte.

Jetzt ist /etc/hostapd/ft8-ap.conf nur noch die Vorlage. Dieses Skript
setzt SSID und Passphrase aus network.ap_fallback der config.yaml ein und
schreibt das Ergebnis nach /run/ft8-ap.conf (Modus 600, nur root). Die
Oberflaeche zeigt damit genau den Wert an, den hostapd benutzt.

Ohne gueltige Passphrase endet es mit Exit 1, und der Hotspot startet
nicht. Lieber kein Notfall-WLAN als eines mit bekannter Passphrase —
Kabel und Tailscale bleiben davon unberuehrt.

Laeuft als root aus start-ap-fallback.sh, VOR jedem Eingriff ins WLAN.
Nur System-Python und python3-yaml, nichts aus der App-Umgebung.

    render-ap-conf.py [VORLAGE [KONFIG [ZIEL]]]
"""
from __future__ import annotations

import os
import sys

import yaml

VORLAGE = "/etc/hostapd/ft8-ap.conf"
KONFIG = "/etc/ft8-appliance/config.yaml"
ZIEL = "/run/ft8-ap.conf"

# Stehen oder standen im Repo bzw. als Vorgabe im Code — nie verwenden.
BEKANNT = {"changeme-please", "ft8setup1"}


def fehler(text: str) -> int:
    print(f"render-ap-conf: {text}", file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    vorlage, konfig, ziel = (argv + [VORLAGE, KONFIG, ZIEL][len(argv):])[:3]
    try:
        with open(konfig, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        return fehler(f"{konfig} nicht lesbar: {exc}")
    ap = ((cfg.get("network") or {}).get("ap_fallback") or {})
    psk = str(ap.get("psk") or "")
    ssid = str(ap.get("ssid") or "ft8-hotspot")

    # Zeilenumbrueche wuerden eine zweite hostapd-Zeile einschleusen.
    if any(c in psk + ssid for c in "\r\n\0"):
        return fehler("SSID oder Passphrase enthaelt einen Zeilenumbruch")
    if not 8 <= len(psk) <= 63:
        return fehler("keine gueltige Passphrase in network.ap_fallback.psk "
                      "(8 bis 63 Zeichen) — Hotspot startet nicht. "
                      "In der Oberflaeche unter Netzwerk setzen.")
    if psk in BEKANNT:
        return fehler("Passphrase ist ein oeffentlich bekannter Platzhalter — "
                      "Hotspot startet nicht.")
    if not 1 <= len(ssid.encode("utf-8")) <= 32:
        return fehler("SSID muss 1 bis 32 Byte lang sein")

    try:
        with open(vorlage, encoding="utf-8") as f:
            zeilen = f.readlines()
    except OSError as exc:
        return fehler(f"Vorlage {vorlage} nicht lesbar: {exc}")

    gesetzt = set()
    aus = []
    for z in zeilen:
        if z.startswith("wpa_passphrase="):
            z = f"wpa_passphrase={psk}\n"
            gesetzt.add("psk")
        elif z.startswith("ssid="):
            z = f"ssid={ssid}\n"
            gesetzt.add("ssid")
        aus.append(z)
    if gesetzt != {"psk", "ssid"}:
        return fehler(f"Vorlage {vorlage} ohne ssid=/wpa_passphrase=-Zeile")

    tmp = f"{ziel}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.writelines(aus)
    os.chmod(tmp, 0o600)
    os.replace(tmp, ziel)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
