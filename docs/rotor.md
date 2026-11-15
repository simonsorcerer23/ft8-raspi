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

- Gedreht wird nur im Hunting, im Leerlauf, ohne laufendes QSO und ohne
  Aussendung. Im CQ-Betrieb bleibt die Antenne stehen.
- Während der Drehung fängt die Station nichts Neues an: kein Anruf, kein
  CQ (dieselbe Pause wie vor einem Self-Update).
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
4. Von Hand prüfen, bevor die Station dreht:

   ```bash
   echo p | nc -q1 127.0.0.1 4533        # muss die Skala des Steuergeraets zeigen
   echo 'P 90 0' | nc -q1 127.0.0.1 4533 # dreht nach Osten — am Geraet zusehen
   ```

5. In der Config:

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
