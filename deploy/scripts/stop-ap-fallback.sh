#!/usr/bin/env bash
# Leave access-point mode: hostapd + dnsmasq down, captive NAT rule gone,
# wlan0 back to NetworkManager (which then reconnects to a known WLAN).
set -uo pipefail

IFACE=wlan0

systemctl stop ft8-hostapd.service || true
systemctl stop dnsmasq || true
nft delete table inet ft8_captive 2>/dev/null || true
ip addr flush dev "${IFACE}" || true
nmcli device set "${IFACE}" managed yes || true
echo "AP-fallback stopped, ${IFACE} back to NetworkManager"
