#!/usr/bin/env bash
# Tear down AP-fallback and hand wlan0 back to NetworkManager.

set -euo pipefail

systemctl stop hostapd@ft8-ap.service || true
systemctl stop dnsmasq || true

nft delete table inet ft8_captive 2>/dev/null || true

ip addr flush dev wlan0
ip link set wlan0 down
nmcli device set wlan0 managed yes || true

echo "AP-fallback stopped; wlan0 returned to NetworkManager"
