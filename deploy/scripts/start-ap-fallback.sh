#!/usr/bin/env bash
# Switch wlan0 from client-mode to access-point mode.
# Triggered by the controller (ap-fallback watchdog, Audit 2026-09-06 A4) when
# no upstream connection — neither WiFi nor Ethernet — has been up for
# network.fallback_delay_s, or by hand via POST /api/network/ap-fallback/start.
#
# Does NOT touch /etc/hostapd/ft8-ap.conf: SSID, passphrase and country code
# come from that file as installed; config.yaml's ap_fallback block is not
# rendered into it (deliberate, 2026-09-06 — see deploy/hostapd/ap.conf).

set -euo pipefail

AP_IP=192.168.66.1/24
IFACE=wlan0

# Stop NetworkManager from fighting us for wlan0 — and WAIT until it has
# actually let go. `nmcli device set ... managed no` returns before
# wpa_supplicant releases the interface; hostapd started in that window
# dies with "Could not connect to kernel driver" (seen live 2026-09-06).
nmcli device set "${IFACE}" managed no || true
for _ in $(seq 1 20); do
    if nmcli -t -f DEVICE,STATE device status 2>/dev/null | grep -q "^${IFACE}:unmanaged"; then
        break
    fi
    sleep 0.25
done
sleep 0.5

# Bring up wlan0 with the AP-mode IP
ip addr flush dev "${IFACE}"
ip addr add "${AP_IP}" dev "${IFACE}"
ip link set "${IFACE}" up

# hostapd as our own foreground unit (not Debian's hostapd@ template — see
# deploy/systemd/ft8-hostapd.service for why), then dnsmasq bound to wlan0.
systemctl reset-failed ft8-hostapd.service 2>/dev/null || true
systemctl restart ft8-hostapd.service
systemctl restart dnsmasq

# Install the captive-portal NAT rule
nft -f /etc/nftables.d/ft8-captive.nft

# Verify the AP is really up — a oneshot that "succeeds" while hostapd is
# dead is exactly how this stayed unnoticed. Fail loudly instead.
sleep 3
if ! systemctl is-active --quiet ft8-hostapd.service; then
    echo "AP-fallback FAILED: ft8-hostapd not active" >&2
    journalctl -u ft8-hostapd -n 10 --no-pager -o cat >&2 || true
    exit 1
fi
echo "AP-fallback active on ${AP_IP} (SSID from /etc/hostapd/ft8-ap.conf)"
