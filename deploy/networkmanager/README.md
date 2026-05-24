# NetworkManager Profile

WLAN-Profile werden zur Laufzeit aus `config.yaml` (`network.wifi_priority`)
via `nmcli` angelegt. Es gibt also keine statischen `.nmconnection`-Files
in diesem Verzeichnis — der Sync-Code lebt in
`backend/ft8_appliance/network/nm_sync.py` (Phase G+, später).

Manuelles Anlegen für ersten Boot:

```bash
sudo nmcli connection add type wifi con-name Heimnetz ifname wlan0 \
     ssid "Heimnetz" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "..."
sudo nmcli connection modify Heimnetz connection.autoconnect-priority 100
```
