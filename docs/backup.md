# Sichern und wiederherstellen

Eine laufende Appliance besteht aus mehr als dem Repo: Zugangsdaten, das
Logbuch, die QSL-Karten, WLAN-Profile, die Länderdatenbank. Nichts davon
steht in Git, und manches davon ist nicht wiederbeschaffbar.

**Anlass:** Am 08.09.2026 fiel ein Pi ohne Vorwarnung aus, und es existierte
keine Sicherung — alle API-Schlüssel hätten neu eingegeben werden müssen.

## Was gesichert wird

| Inhalt | Wo | Wiederbeschaffbar? |
|---|---|---|
| `config.yaml` mit allen Zugangsdaten | `/etc/ft8-appliance` | nur von Hand, aus vier Portalen |
| Logbuch `qso.sqlite`, Telemetrie | `/var/lib/ft8-appliance` | nein |
| **QSL-Karten** (JPEG) | `/var/lib/ft8-appliance/qsl` | **theoretisch ja, praktisch sechs Tage** |
| WLAN-Profile, Hotspot-Konfiguration | `/etc/NetworkManager`, `/etc/hostapd` | von Hand |
| `cty.dat` (DXCC-Länderdatenbank) | `<APP_DIR>/data` | ja, von country-files.com |
| `runtime_state.json` (Audio-Gain!) | `/var/lib/ft8-appliance` | nur durch neues Einpegeln |

Die QSL-Karten sind der teuerste Posten: eQSL gibt höchstens sechs Bilder je
Minute heraus, die 6400 Karten brauchten knapp sechs Tage. Die Datenbank
merkt sich außerdem, welche Karte schon geholt wurde — fehlen die Dateien,
holt sie niemand automatisch nach.

## Sichern

```bash
./scripts/backup-appliance.sh [HOST] [ZIELVERZEICHNIS]
```

Vorgabe ist die Station `ft8-pi5` und ein Ziel außerhalb des Repos.
Das Skript arbeitet zweigeteilt:

* **Archiv** (`<Ziel>/<Zeitstempel>/ft8-backup.tgz`, rund 120 MB): alles außer
  den Bildern, täglich neu. Nach dem Packen wird geprüft, ob die vier
  wichtigen Dateien enthalten und ob `qso.sqlite` heil ist (`integrity_check`)
  — eine Sicherung fällt sonst erst an dem Tag auf, an dem man sie braucht.
* **Spiegel** (`<Ziel>/qsl-spiegel/`): die QSL-Karten, per `rsync` und ohne
  `--delete`. Ein Bild ändert sich nie, nachdem es da ist; der zweite Lauf
  überträgt also nichts mehr. Kein `--delete`, weil eine Sicherung, die
  Löschungen mitzieht, gegen genau den Fall blind ist, für den man sie hat.

Alte Stände werden aufgeräumt (`FT8_BACKUP_BEHALTEN`, Vorgabe 14). Von Hand
benannte Verzeichnisse bleiben liegen — nur Zeitstempel fallen weg.

### Täglich statt „wenn jemand daran denkt"

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd-user/ft8-backup.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ft8-backup.timer
```

Die Units laufen als Benutzer, brauchen kein root und holen einen verpassten
Tag nach. **Der Rechner muss dafür laufen.** Wer das nicht garantieren kann,
legt den Timer auf ein Gerät, das durchläuft, und gibt ihm das Ziel mit.

### Ziel auf einem NAS

Das Skript schreibt in ein Verzeichnis, mehr nicht. Ein NAS ist deshalb kein
Sonderfall:

* **Eingehängte Freigabe:** `./scripts/backup-appliance.sh ft8-pi5 /mnt/nas/ft8-backup`
* **Anderes Gerät:** dasselbe Skript dort einrichten; es braucht nur SSH zur
  Station und `rsync`.

Installationsbezogene Pfade gehören nicht ins Repo. Sie stehen im
Timer beziehungsweise in einer Drop-in-Datei daneben — dasselbe Muster wie
`FT8_PI`/`FT8_PI_SSH` beim Spiegel für dk9xr.de.

## Wiederherstellen

Auf einer **frisch installierten** Appliance (`deploy/install.sh` ist dort
gelaufen):

```bash
./scripts/restore-appliance.sh <Ziel>/<Zeitstempel>/ft8-backup.tgz [HOST]
```

Das Skript stoppt den Controller, spielt Konfiguration, Logbuch und
WLAN-Profile ein, holt die QSL-Karten aus dem Spiegel daneben
(`FT8_QSL_SPIEGEL` überschreibt den Pfad), legt `cty.dat` an ihren Platz,
zieht Rechte gerade und liest zum Schluss den Status zurück.

Bewusst **nicht** eingespielt: `install.env` (gehört zur neuen Installation),
`config.txt` (Pi-Modell-spezifisch) und der Tailscale-Zustand (Neuanmeldung
ist sauberer). Nach dem Einspielen bleibt also: `sudo tailscale up`, Leistung
prüfen, den alten Knoten im Admin-Panel entfernen.

## Was eine Sicherung erst zu einer macht

Ein Backup, das nie zurückgespielt wurde, ist eine Hoffnung. Der Weg hier
wurde beim Umzug auf den Pi 5 am 09.09.2026 einmal vollständig benutzt.
Wer ihn erneut proben will, nimmt ein zweites Gerät, installiert dort
`deploy/install.sh`, spielt den letzten Stand ein und vergleicht Logbuch,
Kartenzahl und Konfiguration mit der laufenden Station.
