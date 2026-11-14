# Yaesu FT-817/818 am Digirig Mobile

Stand 2026-09-14, vorbereitet ohne angeschlossenes Gerät. Quellen: hamlib 4.6.2
auf dem Pi (`rigctl -l`, `rigctl -m 1020 --dump-caps`) und die Digirig-Doku
(digirig.net). Was dort nicht steht, steht auch hier nicht.

## Was die Appliance dafür kann

| | IC-7300 | FT-817/818 am Digirig |
|---|---|---|
| hamlib-Modell | 3073 | 1020 (FT-817) / 1041 (FT-818) |
| CAT | USB-Kabel, 19200 Baud | MiniDin8-Kabel an den Digirig, 4800..38400 Baud, 8N2 |
| PTT | über CAT | über die RTS-Leitung des Digirig-Ports (`--ptt-type=RTS`) |
| Audio | Icom-USB-Codec (`CODEC`) | CM108-Soundkarte des Digirig |
| Leistung | setzbar, 100 W | **nur lesbar**, 0,5/1/2,5/5 W am Gerät |
| Filterbreite | setzbar (2700 Hz) | nicht per CAT; Modus ohne Breite |
| SWR / S-Meter | lesbar (S-Meter defekt) | lesbar laut hamlib |

Konfiguration (`config.yaml`):

```yaml
rig:
  model: ft817            # oder ft818
  serial_device: /dev/serial/by-id/usb-Silicon_Labs_CP2102_...   # der Digirig
  # cat_baud und ptt_type kommen aus dem Profil (4800, rts) — nur setzen, wenn
  # am Gerät eine andere CAT RATE steht.
```

Nach dem Umstellen rendert das Self-Update den rigctld-Aufruf neu und startet
`ft8-rigctld` neu (spätestens beim nächsten Timer-Lauf, alle 10 Minuten).

## Bevor der Digirig eingesteckt wird

1. **gpsd darf den Port nicht anfassen.** Auf dem Pi läuft gpsd für den
   u-blox-Empfänger. Mit `USBAUTO="true"` greift er jeden neuen USB-Seriell-
   Port ab und wackelt an RTS, und RTS ist beim Digirig die PTT: das Rig
   sendet ohne Anlass (Digirig-Doku, „Constant transmission"). In
   `/etc/default/gpsd` muss `USBAUTO="false"` stehen; `DEVICES` zeigt auf den
   u-blox-Link. Der Installer setzt das seit v0.151.0 so.
2. **Kabel**: Digirig-Set „Yaesu FT-8xx": MiniDin6 (Audio/PTT) an die Digirig-
   Audio-Buchse, MiniDin8 (CAT) an den seriellen Port; das CAT-Kabel braucht
   Digirig Mobile ab Rev 1.6 in der Logic-Level-Konfiguration (Auslieferung).
3. **Am Gerät**: CAT RATE auf die konfigurierte Baudrate (Werk 4800), Betriebs-
   art DIG, so dass hamlib `PKTUSB` melden kann. Die Menünummern stehen im
   Yaesu-Handbuch; hier absichtlich nicht.
4. **Leistung am Gerät wählen.** Die Appliance liest sie mit und meldet per
   Push, wenn der Wert über dem für Band und Lizenzklasse erlaubten liegt. Der
   Regler in der Oberfläche zeigt bei diesem Rig nur an.

## Nach dem Einstecken prüfen

```bash
ls /dev/serial/by-id/          # CP2102 ohne "IC-7300" im Namen = Digirig
arecord -L | grep plughw       # zweite Karte neben CODEC = Digirig
```

Wenn beide Rigs gleichzeitig hängen, `rig.audio_card_hint` auf den Digirig-
Kartennamen setzen — die Auto-Wahl nimmt sonst die erste Karte. Pegel nach
Digirig-Doku: Aufnahme 20–50 %, Wiedergabe ~80 %, so dass ALC gerade nicht
anspricht; der ALC-Regler der Appliance merkt sich den Wert je Rig-Modell.

## Leistung und SWR: nur während der eigenen Aussendung (seit v0.199.0)

Aus dem hamlib-Quelltext (`rigs/yaesu/ft817.c`, gelesen am 14.11.2026) und
`rigctl --dump-caps` von hamlib 4.7.2:

* `l RFPOWER` ist beim FT-817/818 die Anzeige des Leistungsmessers in Watt
  (0,5 bis 5,0), keine Einstellung zwischen 0 und 1. Die eingestellte
  Leistung lässt sich per CAT nicht lesen.
* Im Empfang gibt hamlib für Leistung und SWR einen Zwischenspeicher zurück.
  Er steht nach dem Start auf 10, und die Leistungsabfrage überschreibt ihn
  mit dem Wattwert.
* Ein Level für die Versorgungsspannung gibt es nicht.

Die Appliance nimmt deshalb bei diesen beiden Profilen
(`RigProfil.leistung_ist_messwert_w`) Leistung und SWR nur, solange sie
selbst sendet. Der SWR-Guard urteilt über den letzten Wert aus einem eigenen
Burst (höchstens zehn Minuten alt), sonst über keinen. Bis v0.198.0 hätte
die Station im Empfang „50 W" angezeigt und der SWR-Guard vor der ersten
Aussendung gesperrt. Am Gerät ist das nicht geprüft.

## Was nicht geprüft ist

Alles oben ist aus hamlib-Fähigkeiten und Doku abgeleitet, nicht am Gerät
getestet: ob `M PKTUSB 0` beim FT-817 sauber durchgeht, ob das S-Meter
brauchbare Werte liefert, und wie der Digirig-Kartenname unter ALSA lautet.
Beim ersten Anschluss das Journal von `ft8-controller` und `ft8-rigctld`
lesen.
