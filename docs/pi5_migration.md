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
1. Zusammenbauen: NVMe aufs Argon-Board, RTC-Batterie an den Pi 5. Der
   Batterieanschluss ist der **zweipolige** JST-SH zwischen HDMI0 und dem
   USB-C-Stromanschluss, auf der Platine mit `BAT` beschriftet (nicht der
   vierpolige `FAN` und nicht der dreipolige `UART`); rotes Kabel ist Plus.
   **Wichtig bei der verbauten CR2032:** Das ist eine nicht wiederaufladbare
   Primaerzelle. Die Ladeschaltung des Pi 5 ist ab Werk aus und muss es
   bleiben — `dtparam=rtc_bbat_vchg=...` gehoert **nicht** in die
   `config.txt`. Raspberry Pi dazu: "it has a trickle charge circuit which is
   disabled by default. If enabled, this will kill the cell quickly." Die
   CR2032 haelt mit ~220 mAh ohnehin laenger als die offizielle ML2020 (~45 mAh),
   nur eben ohne Nachladen.
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

## Wie der Umzug am 9.9.2026 tatsächlich lief (Korrekturen zum Plan oben)

Der Plan oben ging von der Erstkonfiguration über den Raspberry Pi Imager aus.
Das schlug fehl; hier steht, was wirklich funktioniert hat.

**1. `custom.toml` wirkt unter Trixie nicht mehr.** Das aus Bookworm bekannte
Format wird nicht mehr ausgewertet — `raspberrypi-sys-mods/init_config` gibt es
nicht mehr, Trixie nutzt cloud-init. Der Pi bootete darum als `raspberrypi`
ohne SSH. Was greift, sind zwei Wege parallel auf der Boot-Partition:

* `ssh` (leere Datei) und `userconf.txt` (`benutzer:<yescrypt-Hash>`) —
  ausgewertet von `sshswitch.service` und `userconfig.service`
* `user-data` + `meta-data` für cloud-init (Datenquelle `NoCloud`); darüber
  lassen sich Hostname, SSH-Public-Key, `NOPASSWD`-sudo und die Gruppen
  (`dialout` fürs Rig, `audio` für ALSA) gleich mitgeben

`meta-data` muss existieren, sonst erkennt cloud-init die Quelle nicht.
**Nach dem ersten Start die Dateien von der Boot-Partition löschen** — der
Passwort-Hash hat auf einer offenen FAT-Partition nichts verloren.

**2. NVMe: von der SD aus klonen, nicht neu installieren.** Ablauf:
`parted` (msdos, 512 MiB fat32 + Rest ext4) → `mkfs` → `rsync -aHAXx` für `/`
und getrennt `/boot/firmware` → in `cmdline.txt` und `fstab` die neuen
PARTUUIDs eintragen (`blkid -s PARTUUID`). Danach `BOOT_ORDER=0xf416`
(NVMe zuerst, SD als Rückfallebene) per `rpi-eeprom-config --apply`.
Vorher `rpi-eeprom-update -a` und neu starten.

**Der Test, ob die NVMe wirklich bootet, geht mit noch steckender SD-Karte:**
Bootreihenfolge umstellen, neu starten, `findmnt -no SOURCE /` muss
`/dev/nvme0n1p2` zeigen. Erst dann die Karte ziehen.

Die NVMe läuft im Argon NEO 5 mit PCIe Gen 2 x1 (~500 MB/s). Das reicht;
`dtparam=pciex1_gen=3` bleibt aus, Stabilität geht bei einem Dauerläufer vor.

**3. WLAN muss vor dem Kabelziehen eingerichtet sein.** Das frische Image
kennt nur `eth0`. Erst `raspi-config nonint do_wifi_country DE` (ohne
Funkland bleibt `wlan0` auf `unavailable`), dann die Profile aus dem Backup
nach `/etc/NetworkManager/system-connections/` (root:root, 0600) und
`nmcli connection reload`.

**4. `install.sh --enable-services` bricht ohne Rig ab**, weil `ft8-rigctld`
nicht starten kann. Der Autostart lässt sich trotzdem setzen:
`sudo systemctl enable ft8-rigctld ft8-controller ft8-self-update.timer`
(ohne `--now`). Beim nächsten Start mit Rig kommt alles hoch; ohne Rig steht
die Appliance sauber in `TX_LOCKED`.

**5. Falle bei der Fehlersuche: OpenSSHs Missbrauchsbremse.** Wiederholte
Verbindungsversuche im Sekundentakt, `ssh-keyscan` und Portscans lassen sshd
die Quelladresse sperren: die Verbindung wird angenommen und **ohne Banner
sofort geschlossen** (`kex_exchange_identification: Connection reset by peer`),
jeder weitere Versuch verlängert die Sperre. Das sieht aus wie ein Netzwerk-
oder Firewall-Problem, ist aber keines. Unterscheidungsmerkmal: ein Port ohne
Dienst wird sauber mit *Connection refused* abgelehnt und ICMP läuft
verlustfrei — dann arbeitet der TCP-Stack normal. Abhilfe: einige Minuten
nichts tun oder neu starten, danach **einzeln** anklopfen.

**6. Tailscale:** Knoten heißt `ft8-pi5` (der alte `ft8` bleibt zunächst
stehen), Installation über das signierte APT-Repository statt `curl | sh`.
Im Admin-Panel **key expiry deaktivieren**, sonst fliegt die Appliance nach
Ablauf des Schlüssels aus dem Tailnet.

**7. Am Zielort `install.sh` erneut laufen lassen.** Mehrere Schritte des
Installers hängen an tatsächlich angeschlossener Hardware und werden ohne sie
stillschweigend übersprungen:

* `/etc/default/ft8-rigctld` (Hamlib-Modell, Schnittstelle, Baudrate) — ohne
  die Datei startet `ft8-rigctld` nicht, die Appliance bleibt in `TX_LOCKED`
* `/etc/default/gpsd` (`DEVICES`, `GPSD_OPTIONS="-n -G"`) — ohne das schreibt
  gpsd nichts in die Shared-Memory-Schnittstelle, aus der chrony die
  Satellitenzeit liest; die Uhr läuft dann rein über Netzwerk-NTP

Beim Aufbau in der Firma ohne Rig und GPS blieben beide aus. Wer die
Appliance an einem Ort vorbereitet und an einem anderen aufbaut, führt daher
nach dem Anschließen der Hardware einmal `sudo ./deploy/install.sh` aus
(idempotent) — oder rendert die beiden Dateien gezielt nach. Ohne
`--enable-services`, sonst bricht der Lauf am Start von `ft8-rigctld` ab,
falls das Rig noch nicht antwortet.

Zur Einordnung: Die Satellitenzeit über USB streut rund 200 ms und wird von
chrony darum nur als Rückfallebene gehalten; die Netzquellen liegen bei
Mikrosekunden. Für FT8 mit seinem 15-s-Raster reicht beides mit weitem
Abstand — der Wert des GPS liegt im Ausfall des Netzes.

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
