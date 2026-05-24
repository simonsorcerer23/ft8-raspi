# Operations Guide

Dieses Dokument beschreibt die laufende Beobachtung und Diagnose der FT8-Appliance — insbesondere die **Shake-Down-Phase** (Pi steht daheim am IC-705, läuft Tage am Stück, Claude prüft regelmäßig auf Anfrage).

---

## 1. SSH-Zugriff einrichten (einmalig)

Der Pi wird mit aktiviertem SSH-Server ausgeliefert. Damit Claude (über das Bash-Tool auf der Workstation) **ohne Passwort-Prompt** auf den Pi zugreifen kann:

```bash
# Auf der Workstation, einmalig:
ssh-keygen -t ed25519 -C "ft8-appliance-workstation"    # falls noch kein Key
ssh-copy-id pi@ft8.local                                 # Pubkey installieren
ssh pi@ft8.local 'hostname && uptime'                    # Funktioniert ohne Passwort?
```

### Komfort-Alias (empfohlen)

In `~/.ssh/config` der Workstation:

```sshconfig
Host ft8
    HostName ft8.local
    User pi
    IdentityFile ~/.ssh/id_ed25519
    ServerAliveInterval 30
    ServerAliveCountMax 3
    ConnectTimeout 10
```

Dann reicht `ssh ft8` statt `ssh pi@ft8.local`.

---

## 2. Der "Pi-Check" Workflow (für Shake-Down)

**Konzept:** Du schreibst Claude alle paar Stunden eine kurze Message wie *"check mal den Pi"*, *"alles ok?"*, *"wie sieht's aus?"*. Claude führt dann automatisch eine standardisierte Inspection aus und meldet zurück mit:

- **Grünem Status** wenn alles läuft (kurze Bestätigung + ein paar Kernzahlen)
- **Detail-Analyse** wenn was nicht stimmt (Logs, mögliche Ursachen, Empfehlung)
- **Vergleichs-Hinweisen** zur letzten Inspektion wenn relevant (z.B. "Decode-Rate halbiert seit gestern, Antenne checken?")

### Trigger-Phrasen (alles davon löst den Pi-Check aus)

- "check Pi" / "Pi-Check" / "schau mal" / "alles ok?"
- "wie geht's dem Pi?" / "Pi-Status" / "Status?"
- "läuft alles?" / "noch alles grün?"
- "Was macht der Raspi?"

### Was Claude prüft

Eine einzige zusammengefasste Bash-Pipeline läuft via SSH, deckt alles ab:

```bash
ssh ft8 'bash -s' < scripts/pi-check.sh
```

Inhalt (gekürzt — vollständig in `scripts/pi-check.sh`):

| Bereich | Was wird geprüft |
|---|---|
| **System** | uptime, load avg, free RAM, disk, CPU-Temp, throttling-Flags |
| **Zeit** | chrony tracking, GPS-Fix-Status, DT-Offset |
| **Services** | `systemctl is-active` für ft8-controller, rigctld, gpsd, chrony, hostapd (ggf.), NetworkManager |
| **Netzwerk** | aktives WLAN-Profil, IP, Internet-Reachability, Latenz |
| **Rig** | rigctld erreichbar, IC-705 antwortet, SWR aktuell, Akku-Spannung |
| **App** | letzte Decode-Zeit, QSO-Count heute, Fehler-Count letzte 24h |
| **Logs** | Tail der letzten Errors aus journald, App-Log |

### Antwort-Format (was Claude liefert)

**Grün-Fall (alles ok):**
```
Pi-Check 14:32 — alles grün
  uptime 3d 4h, load 0.4, RAM 1.2/16 GB, Temp 52°C
  GPS fix 3D, DT ±50 ms
  Services: alle active
  Heute: 23 QSOs, 412 Decodes, 0 Errors
  letzte Decode vor 12 s, letzter QSO vor 1h 4m
```

**Gelb-Fall (Anomalie):**
```
Pi-Check 14:32 — Auffälligkeit
  Decode-Rate seit 2h auf 0, vorher ~80/h
  → Audio-Stream weg? Antenne?
  Vorschlag: ssh ft8 'aplay -l' prüfen, ggf. USB neu plugged
  Sonst alles ok.
```

**Rot-Fall (akut):**
```
Pi-Check 14:32 — PROBLEM
  ft8-controller.service: failed since 14:18
  Letzter Error: "ALSA capture device disappeared"
  Pi insgesamt online, SSH ok, andere Services laufen.
  Empfehlung: USB-Kabel IC-705 prüfen, dann
    ssh ft8 'sudo systemctl restart ft8-controller'
```

---

## 3. Manuelle Inspection-Befehle (für Direkt-SSH)

Falls du selbst mal hinschauen willst ohne Claude:

```bash
ssh ft8 systemctl status ft8-controller
ssh ft8 journalctl -u ft8-controller -n 200 --no-pager
ssh ft8 journalctl -u ft8-controller -p err --since "1 hour ago"
ssh ft8 'gpspipe -n 5 -w | head'                    # GPS-Daten
ssh ft8 'chronyc tracking; chronyc sources'         # Zeit-Status
ssh ft8 'sensors 2>/dev/null; vcgencmd measure_temp; vcgencmd get_throttled'
ssh ft8 'nmcli -t -f NAME,DEVICE,STATE connection show --active'
ssh ft8 sqlite3 /var/lib/ft8-appliance/qso.sqlite \
   "select count(*) qso_today from qso where date(qso_start)=date('now')"
```

---

## 4. Recovery-Aktionen (was Claude empfehlen darf)

Claude darf **vorschlagen**, soll aber niemals destruktive Aktionen autonom ausführen ohne deine Bestätigung. Standard-Recovery-Befehle:

| Symptom | Empfohlene Aktion |
|---|---|
| Controller-Service tot | `sudo systemctl restart ft8-controller` |
| USB-Audio weg | Physisch USB-Kabel checken, dann Service-Restart |
| GPS keine Fixes | VK-162 Sichtkontakt zum Himmel? Kabel? `sudo systemctl restart gpsd` |
| Hohe CPU-Temp | Aktive Kühlung? Argon-Fan läuft? |
| Plattenplatz knapp | `journalctl --vacuum-time=7d` + Decode-Rolling-Window verkleinern |
| Hänger im State Machine | `curl -X POST http://ft8.local/api/control/reset-state` |
| Komplett-Reboot | `sudo reboot` |

---

## 5. Längerfristige Trend-Analyse

Claude führt **kein eigenes State-Tracking** zwischen Sessions (Memory ist für Fakten, nicht für Messwerte). Trend-Vergleiche basieren auf der SQLite-DB selbst:

```bash
# QSO-Verlauf der letzten 7 Tage
ssh ft8 sqlite3 /var/lib/ft8-appliance/qso.sqlite \
  "select date(qso_start), count(*) from qso where qso_start > date('now','-7 days') group by 1"

# Decode-Rate pro Stunde der letzten 24h
ssh ft8 sqlite3 /var/lib/ft8-appliance/qso.sqlite \
  "select strftime('%Y-%m-%d %H', ts) h, count(*) from decode where ts > datetime('now','-1 day') group by 1"
```

Diese Queries sind im `scripts/pi-check.sh` integriert, so dass jeder Check eh die letzten Trends mitliefert.

---

## 6. TX-Power Safety-Floor (seit v0.2.3)

Bei jedem Reset-Event (Boot, Operator-Wechsel, Rig-Wechsel,
Bandwechsel) wird die TX-Leistung **runter geclamped** auf
`max(1, effective_max_power_w(band) // 2)` — aber nur wenn aktuell
darüber. QRP-Settings darunter bleiben unangetastet (Variante B).

Details + Edge-Cases: siehe `docs/tx_power_safety.md`.

**Praktische Konsequenz für den Pi-Check:** wenn nach einem Restart
die TX-Power im UI plötzlich auf 50 W steht obwohl du vorher 80 W
hattest — das ist Absicht, nicht Bug. Im Log: `tx-power safety-floor
(boot): clamp 80W -> 50W`.

---

## 7. Sicherheits-Hinweis

- SSH-Key ist auf der **Workstation** zuhause. Im Feldeinsatz kommt Claude *eh nicht* drauf, da nicht im gleichen Netz.
- Der Pi hat **kein** Internet-erreichbares SSH (kein Port-Forwarding, kein Tailscale, kein WireGuard im MVP).
- Falls Remote-Access aus dem Urlaub später gewünscht: Tailscale-Integration als Phase 2 (siehe `architecture.md` Out-of-Scope).
