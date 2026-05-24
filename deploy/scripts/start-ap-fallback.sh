#!/usr/bin/env bash
# Switch wlan0 from client-mode to access-point mode.
# Triggered by the controller when no upstream WiFi has been reachable
# for fallback_delay_s seconds (see config.yaml -> network.fallback_delay_s).

set -euo pipefail

AP_IP=192.168.66.1/24

# Stop NetworkManager from fighting us for wlan0
nmcli device set wlan0 managed no || true

# Bring up wlan0 with the AP-mode IP
ip addr flush dev wlan0
ip addr add ${AP_IP} dev wlan0
ip link set wlan0 up

# Start hostapd + dnsmasq, configured to bind wlan0 only
systemctl restart hostapd@ft8-ap.service || systemctl start hostapd@ft8-ap.service
systemctl restart dnsmasq

# Install the captive-portal NAT rule
nft -f /etc/nftables.d/ft8-captive.nft

echo "AP-fallback active on ${AP_IP}"
