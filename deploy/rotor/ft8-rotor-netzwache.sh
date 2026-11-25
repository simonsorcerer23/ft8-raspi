#!/bin/bash
# Netzwache fuer den Zero 2 W am Rotor (ft8-rotor).
#
# Am 24.11.2026 12:41 stuerzte der WLAN-Chip (brcmfmac ueber SDIO) ab:
# "bus is down", "mmc1: card 0001 removed". Die Karte meldete sich neu,
# wlan0 kam aber nicht zurueck, und der Zero lief 22 Stunden ohne Netz,
# bis jemand den Stecker zog. Ohne Bildschirm und Tastatur ist das
# Netz der einzige Weg hinein.
#
# Alle 5 Minuten: Router erreichbar -> gut. Sonst erst das WLAN neu
# verbinden, dann den Treiber neu laden, nach 15 Minuten ohne Netz neu
# starten — aber hoechstens einmal je Stunde Laufzeit, damit ein
# ausgefallener Router keine Neustartschleife ausloest.
set -u
ZAEHLER=/run/ft8-rotor-netzwache
ziel=$(ip -4 route show default | awk '{print $3; exit}')
ziel=${ziel:-192.168.1.1}

if ping -c 2 -W 3 "$ziel" >/dev/null 2>&1; then
    [ -f "$ZAEHLER" ] && echo "Netz wieder da ($ziel)"
    rm -f "$ZAEHLER"
    echo "ok"
    exit 0
fi

n=$(( $(cat "$ZAEHLER" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$ZAEHLER"
echo "Router $ziel nicht erreichbar, Runde $n"

case "$n" in
    1)
        nmcli radio wifi off; sleep 3; nmcli radio wifi on
        ;;
    2)
        modprobe -r brcmfmac_wcc brcmfmac 2>/dev/null
        sleep 3
        modprobe brcmfmac
        ;;
    *)
        laufzeit=$(cut -d. -f1 /proc/uptime)
        if [ "$laufzeit" -ge 3600 ]; then
            echo "15 min ohne Netz, starte neu"
            systemctl reboot
        else
            echo "kein Neustart, Laufzeit erst ${laufzeit}s"
        fi
        ;;
esac
exit 0
