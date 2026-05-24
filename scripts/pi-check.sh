#!/usr/bin/env bash
# Health-Check der FT8-Appliance — wird per SSH vom Workstation ausgeführt.
# Aufruf:  ssh ft8 'bash -s' < scripts/pi-check.sh
# Output ist als Eingabe für Claudes Pi-Check-Workflow gedacht (struktur. Block-Tags).

set -u

APP_DIR="/opt/ft8-appliance"
DB="${APP_DIR}/data/appliance.db"

section() { printf '\n=== %s ===\n' "$1"; }

# ---------------------------------------------------------------------------
section SYSTEM
echo "host: $(hostname)"
echo "uptime: $(uptime -p) (since $(uptime -s))"
echo "load: $(cut -d' ' -f1-3 /proc/loadavg)"
echo "mem: $(free -h | awk '/^Mem:/ {printf "%s used / %s total", $3, $2}')"
echo "disk: $(df -h / | awk 'NR==2 {printf "%s used / %s total (%s)", $3, $2, $5}')"
if command -v vcgencmd >/dev/null; then
    echo "cpu_temp: $(vcgencmd measure_temp | cut -d= -f2)"
    echo "throttled: $(vcgencmd get_throttled)"
fi

# ---------------------------------------------------------------------------
section TIME
if command -v chronyc >/dev/null; then
    chronyc -n tracking | grep -E 'Reference|System time|RMS|Stratum' || true
    # Hard alarm if System-time offset > 0.5s (FT8 DT-Guard threshold)
    offset_s="$(chronyc -n tracking 2>/dev/null \
        | awk '/System time/ {print $4}')"
    if [ -n "$offset_s" ]; then
        if awk -v o="$offset_s" 'BEGIN{exit !(o+0 > 0.5)}'; then
            echo "ALARM: chrony offset ${offset_s}s exceeds FT8 DT threshold (0.5s)"
        else
            echo "offset_ok: ${offset_s}s (< 0.5s)"
        fi
    fi
    echo "--- sources ---"
    chronyc -n sources 2>/dev/null | head -10 || true
fi

# ---------------------------------------------------------------------------
section GPS
if command -v gpspipe >/dev/null; then
    timeout 3 gpspipe -n 3 -w 2>/dev/null \
        | python3 -c '
import json, sys
for line in sys.stdin:
    try:
        d = json.loads(line)
    except Exception:
        continue
    if d.get("class") == "TPV":
        # Single-quotes innerhalb f-strings, weil escapte double-quotes
        # in Python f-strings vor 3.12 SyntaxError werfen — und das
        # ist auch in 3.13+ unnoetig komplex. Sebastian 2026-05-24.
        print(f"fix mode={d.get('mode')} lat={d.get('lat')} lon={d.get('lon')} alt={d.get('alt')} time={d.get('time')}")
        break
    if d.get("class") == "SKY":
        sats = d.get("satellites", [])
        used = sum(1 for s in sats if s.get("used"))
        print(f"satellites seen={len(sats)} used={used}")
' || echo "gpspipe: timeout or no data"
else
    echo "gpspipe not installed"
fi

# ---------------------------------------------------------------------------
section SERVICES
for s in ft8-controller ft8-rigctld gpsd chrony NetworkManager hostapd; do
    state="$(systemctl is-active "$s" 2>/dev/null || echo unknown)"
    enabled="$(systemctl is-enabled "$s" 2>/dev/null || echo unknown)"
    printf "%-20s state=%-10s enabled=%s\n" "$s" "$state" "$enabled"
done

# ---------------------------------------------------------------------------
section NETWORK
if command -v nmcli >/dev/null; then
    nmcli -t -f NAME,DEVICE,TYPE,STATE connection show --active
fi
echo "--- reachability ---"
ip="$(hostname -I | awk '{print $1}')"
echo "local ip: $ip"
ping -c 1 -W 2 1.1.1.1 >/dev/null 2>&1 && echo "internet: OK" || echo "internet: DOWN"

# ---------------------------------------------------------------------------
section RIG
if nc -z localhost 4532 2>/dev/null; then
    echo "rigctld port 4532: open"
    # Frequency
    echo -e "f\nq" | nc -q1 localhost 4532 2>/dev/null | head -1 \
        | awk '{print "freq_hz:", $1}'
else
    echo "rigctld port 4532: closed"
fi

# ---------------------------------------------------------------------------
section APP
if [ -f "$DB" ]; then
    sqlite3 "$DB" <<'SQL'
.mode column
.headers on
SELECT
  (SELECT COUNT(*) FROM qso WHERE date(qso_start)=date('now')) AS qso_today,
  (SELECT COUNT(*) FROM qso WHERE qso_start > datetime('now','-7 days')) AS qso_7d,
  (SELECT COUNT(*) FROM decode WHERE ts > datetime('now','-1 hour')) AS dec_1h,
  (SELECT COUNT(*) FROM decode WHERE ts > datetime('now','-1 day')) AS dec_24h;
SQL
    echo "--- last decode ---"
    sqlite3 "$DB" "select ts, call_from, message, snr_db, band from decode order by ts desc limit 1"
    echo "--- last qso ---"
    sqlite3 "$DB" "select qso_start, call, band, rst_rcvd, grid_rcvd from qso order by qso_start desc limit 1"
else
    echo "db not present yet: $DB"
fi

# ---------------------------------------------------------------------------
section LOGS
echo "--- ft8-controller errors last 1h ---"
journalctl -u ft8-controller -p err --since "1 hour ago" --no-pager -n 30 2>/dev/null | tail -30 \
    || echo "(no journald access or no errors)"
echo "--- last 10 lines ft8-controller ---"
journalctl -u ft8-controller --no-pager -n 10 2>/dev/null | tail -10 \
    || echo "(no journald access)"

echo
echo "=== END pi-check $(date -Is) ==="
