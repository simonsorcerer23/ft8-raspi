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
# Ask NetworkManager to pick the best known WLAN right away. Usually it does
# so by itself, but a profile that was taken down by hand (nmcli connection
# down) stays autoconnect-blocked until asked — seen 2026-09-06 while testing
# the watchdog: AP stopped, wlan0 back, nothing reconnected.
sleep 1
nmcli device connect "${IFACE}" >/dev/null 2>&1 || true
echo "AP-fallback stopped, ${IFACE} back to NetworkManager"
