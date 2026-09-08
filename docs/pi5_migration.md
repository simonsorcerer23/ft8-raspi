# Umzug auf den Pi 5 (Runbook, 2026-09-08)

Anlass: Der Pi 4B verstummte am 8.9. um 10:21 ohne Log-Spur (10 min nach dem
Update auf v0.81.0 mit der jt9-Stufe) und lief erst nach einem Stromreset
wieder. Vorher lief er 40 Tage durch. Ersatz: Raspberry Pi 5 8 GB im
Argon NEO 5 M.2 mit NVMe-SSD, offiziellem 27-W-Netzteil und RTC-Batterie.

**Vor dem Umbau immer zuerst:** `./scripts/backup-appliance.sh ft8`
(liegt in `~/.config/codex/secrets/ft8-backup/<Zeitstempel>/`, nie ins Repo).

## Was aus dem Backup übernommen wird

| Datei | Inhalt | warum sie zählt |
|---|---|---|
| `config.yaml` | QRZ-Zugang, ClubLog-Key, PSK-Reporter, ntfy-Topics, API-Token, Rig-Serial + CI-V-Baud, Bänder, Antenne, alle Strategie-Schalter | ohne sie müsste alles neu eingegeben werden |
| `runtime_state.json` | eingeregelter Audio-Gain je Rig-Modell (aktuell `ic7300: 0.291`) | sonst regelt die ALC-Schleife von vorn los |
| `qso.sqlite` | QSOs, Heard-Liste, **Pick-Telemetrie** (611 Zeilen), Ruf-Reputation | die Telemetrie ist die Grundlage der Antwortstrategie |
| `system-connections/*` | WLAN-Profile samt Passwörtern (`reisprivat`, `Altec`) | sonst kommt der Pi am neuen Standort nicht ins Netz |
| `ft8-ap.conf` | Hotspot-SSID `ft8-hotspot` + PSK | Zugang, wenn kein WLAN da ist |

Nicht übernommen: `install.env` (erzeugt `install.sh` neu), `config.txt`
(Pi-5-spezifisch), Tailscale-State (Neuanmeldung ist sauberer).

## Aufbau an einem anderen Ort (Firma), Rig kommt später

Der neue Pi muss nicht am Rig stehen, um eingerichtet zu werden — das Backup
liegt auf dem PC, nicht auf dem alten Pi. Arbeitsteilung:

**Sebastian am neuen Pi (10 Minuten):**
1. Zusammenbauen: NVMe aufs Argon-Board, RTC-Batterie an den Pi 5.
2. microSD am PC mit dem Raspberry Pi Imager bespielen: **Raspberry Pi OS
   Lite 64-bit**, Hostname `ft8`, Benutzer `sebastian`, SSH mit dem
   öffentlichen Schlüssel aus `~/.ssh/id_ed25519.pub`, WLAN `Altec`
   (das Firmennetz; `reisprivat` kommt später aus dem Backup dazu).
3. Pi mit der microSD starten, `curl -fsSL https://tailscale.com/install.sh | sh`
   und `sudo tailscale up`, den Link im Browser bestätigen. Der Knoten
   erscheint als **`ft8-1`**, solange der alte Pi `ft8` noch online ist.

**Danach alles Weitere per SSH, ohne Sebastian:** NVMe bespielen und
Bootreihenfolge umstellen, `install.sh`, Backup einspielen, prüfen.

**Beim Aufstellen am Rig:** Pi anstecken, IC-7300 an eine schwarze
USB-2.0-Buchse, GPS daneben. Das WLAN `reisprivat` ist dann aus dem Backup
bekannt; kommt er trotzdem nicht ins Netz, öffnet er nach 60 s den Hotspot
`ft8-hotspot`. Zum Schluss den alten Tailscale-Knoten `ft8` löschen und
`ft8-1` umbenennen.

Ohne angeschlossenes Rig sperrt der `rig_link_guard` den Sendebetrieb — das
ist beabsichtigt (seit v0.83.0) und kein Fehler.

## Ablauf (wenn Pi und Rig am selben Ort sind)

1. **Zusammenbau.** NVMe ins Argon-Board, RTC-Batterie an den Pi-5-Anschluss
   (der Pi 5 hat eine echte Uhr — der 4B hatte keine, deshalb bootete er mit
   der Zeit vom 30. Juli und chrony musste 40 Tage nachstellen).
2. **OS.** Raspberry Pi OS **Lite 64-bit** mit dem Imager auf die NVMe (oder
   erst auf microSD, dann von dort die NVMe beschreiben). Im Imager setzen:
   Hostname `ft8`, Benutzer `sebastian`, SSH mit deinem Public-Key, WLAN
   `reisprivat`. Beim Argon NEO 5 M.2 nach dem ersten Boot prüfen, ob
   `/boot/firmware/config.txt` PCIe aktiviert hat (`dtparam=pciex1`), sonst
   ergänzen; Gen 3 (`dtparam=pciex1_gen=3`) ist optional und nicht nötig.
3. **Alten Pi ausschalten**, bevor der neue mit demselben Hostnamen und
   Tailscale-Namen startet.
4. **Installieren:**
   ```bash
   ssh sebastian@ft8
   git clone https://github.com/simonsorcerer23/ft8-raspi.git ~/ft8-appliance
   cd ~/ft8-appliance && sudo ./deploy/install.sh
   ```
   `install.sh` zieht auch das Paket `wsjtx` für die jt9-Decoderstufe.
5. **Daten einspielen** (vom PC aus):
   ```bash
   ./scripts/restore-appliance.sh ~/.config/codex/secrets/ft8-backup/<Stempel>/ft8-backup.tgz ft8
   ```
   Das Skript stoppt den Controller, kopiert Konfiguration, Datenbank und
   WLAN-Profile, richtet Rechte, startet neu und liest den Status zurück.
6. **Tailscale:** `sudo tailscale up` und den Link bestätigen; im Admin-Panel
   den alten Knoten `ft8` entfernen.
7. **Rig anschließen:** IC-7300 an eine **schwarze USB-2.0-Buchse** (USB 3
   streut HF), GPS-Dongle daneben. Der Serial-Pfad in der `config.yaml` ist
   ein `by-id`-Pfad und bleibt damit gültig.
8. **Prüfen:** Frequenz und Betriebsart im Status, ein CQ-Zyklus, ALC im
   grünen Bereich, `decoder_late_pass.jt9` zählt hoch. Leistung nach dem
   ersten Start auf den gewünschten Wert setzen (der Safety-Floor beginnt
   bei 50 W).

## Was auf dem Pi 5 anders sein wird

- **jt9 Tiefe 3** wurde am 4B nur im Sendezyklus riskiert (11–17 s pro Slot).
  Auf dem Pi 5 ist zu messen, ob Tiefe 3 dauerhaft ins 15-s-Budget passt:
  `decoder_jt9_depth: 3` setzen und `decoder_late_pass.jt9.duration_s`
  beobachten; bei über 9 s zurück auf 2.
- **Stromversorgung:** Der Pi 5 verlangt das 27-W-Netzteil, sonst drosselt er
  die USB-Ports. Nach einer Woche `vcgencmd get_throttled` prüfen — `0x0`
  heißt sauber.
- **Kühlung:** Zwei Kerne laufen pro Slot mehrere Sekunden voll. Das
  Argon-Gehäuse kühlt passiv über den Deckel; bei über 80 °C drosselt der Pi
  und der Decoder wird langsam.

## Zur Ausfallursache des 4B (unbewiesen)

`vcgencmd get_throttled` meldete nach dem Reset `0x0`, das Register wird
allerdings bei Stromverlust zurückgesetzt und sagt nichts über vorher. Das
systemd-Journal enthält vom Zeitraum um 10:21 **keine** Einträge mehr: Der
Pi bootete ohne Echtzeituhr mit der Zeit vom 30. Juli, chrony stellte die Uhr
danach um 40 Tage vor, und dabei ging die Zuordnung der alten Einträge
verloren. Kein Kernel-Panic, kein OOM, kein Dateisystemfehler auffindbar;
die SSD ist intakt (`integrity_check ok`). Ein harter Strom- oder
Spannungseinbruch passt zum Bild, beweisen lässt er sich nicht.
