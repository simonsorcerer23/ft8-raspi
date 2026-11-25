# Rotor-Nachführung

Stand 2026-11-15, gebaut ohne Rotor-Interface. Standardmäßig aus
(`rotor.enabled: false`).

## Was sie macht

Die Station dreht die Richtantenne dorthin, wo das Band gerade offen ist
und viel los ist. Sie dreht **nicht** je QSO: Der Rotor braucht für eine
halbe Umdrehung länger als ein FT8-QSO, und ständiges Nachdrehen wäre
Verschleiß ohne Nutzen.

Die Richtung kommt von PSK Reporter, und zwar nicht aus dem, was uns hört
oder was wir hören. Beides hängt davon ab, wohin unsere Antenne gerade
zeigt, und würde die eingestellte Richtung immer bestätigen. Gezählt werden
die Sender, die **andere Empfänger im eigenen 4-stelligen Feld** (am MK2
JN58) in den letzten 15 Minuten gehört haben, je 10-Grad-Fach, ab 1000 km
Entfernung, jedes Rufzeichen einmal. Die Antenne zeigt dann auf das Fenster
von 60 Grad mit den meisten Sendern.

Am 15.11.2026 stimmte diese Quelle mit den eigenen Daten überein: Um 10:38
UTC hörten Empfänger in JN58 auf 15 m vor allem Asien (60 Grad) und
Südamerika/Afrika (240 Grad), dieselbe Verteilung wie die eigenen Decodes
der Vortage um 10 UTC. Über den ganzen Tag (57 000 Decodes, 12.–15.11.):
morgens Japan und Asien bei 30–60 Grad, ab etwa 12 UTC Nordamerika bei
300 Grad, das bis zum Schluss des Bands gegen 20 UTC.

## Regeln

- Nachgeführt wird nur im Hunting; im CQ-Betrieb bleibt die Antenne stehen.
- Entschieden wird auch während eines QSOs — auf einem vollen Band ist fast
  immer eines im Gang, und eine Prüfung, die auf einen freien Moment wartet,
  trifft ihn kaum (das zeigte der Trockenlauf am 15.11. sofort). Ist eine
  Drehung beschlossen, fängt die Station nichts Neues mehr an (kein Anruf,
  kein CQ, dieselbe Pause wie vor einem Self-Update), lässt das laufende QSO
  zu Ende laufen und dreht dann. Wird sie in fünf Minuten nicht frei,
  entfällt die Drehung.
- Geht trotzdem eine Aussendung los (etwa ein 73 auf ein spätes RR73),
  hält der Rotor an und fährt danach weiter. Gedreht wird nie, während
  gesendet wird.
- Nach einer Drehung bleibt die Antenne mindestens 20 Minuten stehen;
  entschieden wird höchstens alle 10 Minuten. Gedreht wird erst bei
  mindestens 30 Grad Unterschied und 25 % mehr Sendern im Ziel.
- Höchstens 1440 Grad Drehweg je UTC-Tag (vier volle Umdrehungen,
  `max_drehweg_tag_grad`); danach bleibt die Antenne bis Mitternacht UTC
  stehen. Der Zähler lebt im Arbeitsspeicher und beginnt nach einem
  Neustart von vorn.
- Ein **erlaubter Bereich** (`bereich_von_grad` bis `bereich_bis_grad`, im
  Uhrzeigersinn gelesen) begrenzt die Nachführung zusätzlich — etwa wenn
  die Kabel am Mast keine volle Umdrehung hergeben oder eine Richtung
  verbaut ist. Beispiel: 200 bis 160 lässt 40 Grad um den Anschlag im Süden
  frei. Der Bereich darf den Endanschlag nicht enthalten, sonst lehnt die
  Konfiguration ihn ab. Innerhalb des Bereichs bleibt auch jeder Drehweg im
  Bereich, weil der Anschlag nie gekreuzt wird.
- Der Endanschlag (beim Create RC5B-3 im Süden, „END" auf der Skala) wird
  nie gekreuzt; von 240 nach 120 Grad geht es über Norden.
- **Handbetrieb hat Vorrang.** Steht die Antenne ohne eigenen Befehl mehr
  als 10 Grad neben der zuletzt angefahrenen Richtung, hat jemand am
  Steuergerät gedreht. Die Nachführung ruht dann zwei Stunden und meldet
  das einmal per Push.
- Kommt der Rotor nicht innerhalb von 150 Sekunden an, hält die Station
  ihn an, ruht ebenfalls und meldet es.

## Trockenlauf

Mit `rotor.trockenlauf: true` (und `enabled: true`) entscheidet die Station
mit echten PSK-Daten, ohne dass ein Rotor angeschlossen sein muss. Die
Antenne gilt nach jeder Entscheidung als am Ziel; neue Anrufe werden nicht
pausiert, Kontrollstunden gibt es nicht. Die Bilanz zeigt im Abschnitt
„Rotor-Nachfuehrung" Drehungen und Drehweg je Tag und die Ziele je Stunde —
so lässt sich vor dem Einbau sehen, ob die Schwellen zu oft oder zu selten
drehen lassen. Trockenlauf-Zeilen (`grund = 'trockenlauf'`) zählen nicht
für die Messung.

## Messung

In jeder zweiten UTC-Stunde wird entschieden und protokolliert, aber nicht
gedreht (Messplan `rotor_nachfuehrung`, Tabelle `rotor_log`, Abschnitt
„Rotor-Nachfuehrung" in `scripts/qso_bilanz.py`). Verglichen werden die
QSOs je Stunde. Ob die Nachführung an bleibt, entscheidet der Betreiber.

## Hardware: Create RC5B-3 am MK2

Das Steuergerät hat keinen Rechneranschluss. Nachrüsten lässt es sich mit
dem **ERC-Interface von DF9GR** (Easy Rotor Control), das hamlib direkt
kennt (`rotctld -m 404`, „DF9GR ERC").

Laut Installationsanleitung des ERC (V4.x, Stand 2024-05-13, Seite 17,
„Create: RC5-3 – RC5A-3 – RC5B-3") wird die Ausführung **ohne „-P"** —
also Raymonds Gerät — ausdrücklich unterstützt, mit Einbau ins Gehäuse:

- Positionssignal an TB-1 Klemme 5, Masse an Klemme 6,
- zwei Anschlüsse am SPEED-Regler,
- die Schalterkontakte für CW und CCW,
- AUX-Relais auf „SPEED".

Beim RS232-Bausatz verlangt die Anleitung eine eigene Versorgung, die nicht
mit der Stationsmasse verbunden ist. Die Ausführungen „-P" haben stattdessen
eine 6-polige DIN-Buchse (Seite 18).

### Eigener Rechner am Rotor: `ft8-rotor` (Raspberry Pi Zero 2 W)

Seit 2026-11-19 läuft `rotctld` nicht am MK2-Pi, sondern auf einem
eigenen Zero 2 W (Hostname `ft8-rotor`, WLAN „Altec“), der beim ERC sitzt.
Der MK2-Pi spricht ihn über das Netz an (`rotor.host`).

- Unit `/etc/systemd/system/ft8-rotctld.service` (Benutzer `rotor`,
  `/usr/bin/rotctld` aus Debian, hamlib 4.6.2, `-T 0.0.0.0`), Werte in
  `/etc/default/ft8-rotctld` (`ROT_MODEL=404`, `-s 9600`). Angelegt, aber
  aus, bis das ERC am USB hängt.
- nftables: Port 4533 nur vom MK2-Pi (192.168.1.175) und über
  `tailscale0`; sonst nur SSH, mDNS, DHCP.
- `wpasupplicant` ist auf 2:2.10-24 festgehalten (`apt-mark hold`):
  2:2.10-24+rpt3 meldet SAE an NetworkManager, und der WLAN-Chip des
  Zero 2 W scheitert im WPA2/WPA3-Netz dann mit „Authentication timed
  out“. Hold erst lösen, wenn Raspberry Pi das behoben hat
  (`/var/local/ft8-rotor/LIESMICH`).
- Netzwache seit 2026-11-25 (`deploy/rotor/`): Am 24.11. um 12:41 stürzte
  der WLAN-Chip ab („brcmf … bus is down“, „mmc1: card 0001 removed“),
  wlan0 kam nicht zurück, und der Zero lief 22 Stunden ohne Netz. Der
  Timer `ft8-rotor-netzwache.timer` pingt alle 5 Minuten den Router;
  fehlt er, verbindet er erst das WLAN neu, lädt dann `brcmfmac` neu und
  startet nach 15 Minuten ohne Netz neu (nur wenn der Zero schon eine
  Stunde läuft). Dazu WLAN-Stromsparen aus
  (`/etc/NetworkManager/conf.d/90-wlan-stromsparen-aus.conf`).

In den Schritten unten heißt „am MK2-Pi“ für `rotctld` dann „am Zero“,
und die Handprüfung läuft vom MK2-Pi aus gegen `ft8-rotor` statt
`127.0.0.1`.

## Inbetriebnahme, wenn das Interface eingebaut ist

1. Interface am USB des MK2-Pi anschließen und den Gerätenamen nachsehen:
   `ls /dev/serial/by-id/`.
2. `/etc/default/ft8-rotctld` anlegen:

   ```
   ROT_MODEL=404
   ROT_DEVICE=/dev/serial/by-id/<ERC>
   ROT_ARGS=
   ```

   Verlangt die ERC-Anleitung eine bestimmte Baudrate, gehört sie als
   `-s <Rate>` in `ROT_ARGS`.
3. Unit aus `deploy/systemd/ft8-rotctld.service.in` rendern
   (`@SERVICE_USER@` = Dienstbenutzer, am MK2 `ft8`), nach
   `/etc/systemd/system/` legen, `systemctl enable --now ft8-rotctld`.
4. **Am Mast klären, bevor irgendetwas dreht:** Wie weit geben Koax und
   Steuerleitung nach, ohne zu ziehen oder sich um den Mast zu wickeln?
   Liegt der Anschlag wirklich im Süden? Steht etwas im Weg (Nachbarmast,
   Abspannung, Baum)? Daraus folgen `endanschlag_grad` und, wenn nötig,
   `bereich_von_grad`/`bereich_bis_grad`. Außerdem die Zeit für eine volle
   Umdrehung bei der eingestellten SPEED-Stufe stoppen und `fahrzeit_max_s`
   etwas darüber setzen (Vorgabe 150 s).
5. Von Hand prüfen, bevor die Station dreht:

   ```bash
   echo p | nc -q1 127.0.0.1 4533        # muss die Skala des Steuergeraets zeigen
   echo 'P 90 0' | nc -q1 127.0.0.1 4533 # dreht nach Osten — am Geraet zusehen
   ```

   Dann knapp beiderseits des Anschlags: `P 170 0` und danach `P 190 0`.
   Der Rotor muss dafür den langen Weg über Norden fahren, nicht 20 Grad
   über Süden. Fährt er über Süden, stimmt die Kalibrierung des Interfaces
   oder `endanschlag_grad` nicht — dann nicht einschalten.

6. In der Config:

   ```yaml
   rotor:
     enabled: true
     antenne: raymond_gross
   ```

   Weitere Felder (Öffnungswinkel, Haltezeit, Toleranzen) stehen mit ihren
   Vorgaben in `RotorConfig` (`backend/ft8_appliance/config/models.py`).

Der Zustand steht in `/api/status` unter `rotor`: aktuelle Richtung, Ziel,
Sender im Öffnungswinkel an beiden Stellen und der Grund der letzten
Entscheidung.
