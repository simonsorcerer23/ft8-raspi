# LoTW-Upload

Stand 2026-09-14, gebaut ohne Zertifikat. Quellen: die ARRL-Hilfeseiten
`lotw-help/cmdline`, `lotw-help/developer-submit-qsos` und
`lotw-help/installation`.

## Warum das anders läuft als eQSL

LoTW nimmt keine Zugangsdaten entgegen. Es akzeptiert nur Logdateien, die
mit dem Callsign-Zertifikat des Funkamateurs signiert sind, und signieren
kann nur die ARRL-Software TQSL. Die Appliance ruft sie deshalb als
Programm auf, statt eine Schnittstelle zu bedienen.

Genau diese Hürde ist der Grund, warum die ARRL LoTW für das DXCC
anerkennt und eQSL nicht: Vor der Ausstellung prüft sie Lizenz und
Identität, und jede Bestätigung ist danach kryptografisch signiert.

## Einmalige Einrichtung

TQSL ist auf dem Pi bereits installiert (Debian-Paket `trustedqsl`, dazu
`xvfb`, weil die Fassung im Paket ein Display verlangt).

Zwei Schritte kann nur der Betreiber machen. Beide sind für DK9XR am
15.09.2026 erledigt; die Beschreibung steht hier für den nächsten
Operator und für den Fall, dass der Pi neu aufgesetzt wird.

1. **Zertifikat beantragen.** Das läuft über TQSL selbst und dauert
   einige Tage. Die ARRL will eine Kopie der Zulassung und ein zweites
   Dokument mit Name und Adresse, per E-Mail an `LoTW-help@arrl.org`.
   Ein Postweg ist nicht nötig. Das Zertifikat gilt drei Jahre; die
   Erneuerung verlangt die Unterlagen nicht erneut.

   Der Antrag enthält ein **QSO-Startdatum**, und das ist etwas anderes
   als die drei Jahre Laufzeit: Es legt fest, welche QSO-Daten überhaupt
   signiert werden dürfen, und gehört auf das erste QSO unter diesem
   Rufzeichen, damit sich auch ein Altlog hochladen lässt. Nachsehen
   lässt es sich am fertigen Zertifikat:

   ```bash
   ssh ft8-pi5 'openssl x509 -in ~/.tqsl/certs/user -noout -text' | grep -A1 12348
   ```

   Die Erweiterungen `…12348.1.2` und `.1.3` sind Anfang und Ende,
   `.1.4` ist die DXCC-Nummer. Für DK9XR: 1990-04-02 bis 2029-09-13,
   DXCC 230. Ein zu enger Bereich wird über die Erneuerung korrigiert,
   nicht über einen Neuantrag — der kollidiert mit dem bestehenden
   Zertifikat.
2. **Zertifikat und Station Location auf dem Pi anlegen.** Beides
   braucht die grafische Oberfläche, also per `ssh -X ft8-pi5` mit
   weitergereichtem Display:

```bash
ssh -X ft8-pi5 'tqsl -i /pfad/zum/zertifikat.tq6'
```

**Der Aufruf kommt nicht von selbst zurück.** TQSL schreibt die
Zertifikate, zeigt danach eine Erfolgsmeldung und wartet auf den Klick
darauf. Kommt kein Fenster (etwa beim Aufruf über `xvfb-run` statt über
`ssh -X`), hängt der Prozess sichtbar untätig. Abbrechen ist in diesem
Fall gefahrlos: nachsehen, ob `~/.tqsl/certs/` die drei Dateien
`authorities`, `root` und `user` enthält, dann ist der Import fertig.

Danach die Station Location anlegen und benennen:

```bash
ssh -X ft8-pi5 'tqsl -s'
```

Rufzeichen und DXCC-Eintrag setzen, Grid eintragen; ITU- und CQ-Zone
füllt TQSL aus dem DXCC und lässt sie grau. IOTA bleibt leer, sofern
nicht von einer Insel gefunkt wird. Das Ergebnis landet in
`~/.tqsl/station_data`.

Der dort vergebene Name gehört anschließend in die Operator-Verwaltung,
Feld „LoTW-Station-Location". Ohne ihn signiert TQSL nicht, und der
Upload-Loop startet gar nicht erst. Mit ihm startet er sofort — das
Eintragen über die Oberfläche genügt, ein Neustart ist nicht nötig.

## Ein Altlog einmalig nachtragen

Für QSOs, die vor der Appliance entstanden sind, gibt es keinen
automatischen Weg — sie stehen in keiner Datenbank, die der Loop kennt.
Der Weg ist eine ADIF-Datei und ein einzelner TQSL-Aufruf. Erst ohne
`-u` signieren, damit sichtbar wird, was TQSL bemängelt:

```bash
ssh ft8-pi5 'xvfb-run -a tqsl -d -a compliant -l Weissenhorn -o /tmp/probe.tq8 -x /tmp/altlog.adi'
```

Läuft das sauber durch, derselbe Aufruf mit `-u` statt `-o …` lädt hoch.
Bereits hochgeladene QSOs weist LoTW als Duplikate ab, das ist Status 8
oder 9 und kein Fehler.

**Achtung bei Portabelbetrieb.** Eine Station Location trägt genau ein
DXCC-Gebiet. QSOs mit `/MM` oder `/AM` gehören nicht dazu und brauchen
eine eigene Location. Der Upload-Loop unterscheidet das derzeit **nicht**
— er filtert die offenen QSOs nach dem Operator, nicht nach dem
Stationsrufzeichen, und würde Schiffs-QSOs mit der Heimatadresse
signieren. Vor dem ersten `/MM`-Betrieb ist das zu ändern.

## Zur Passphrase

Das Zertifikat lässt sich mit oder ohne Passphrase importieren. TQSL
kennt für den unbeaufsichtigten Betrieb nur den Schalter `-p`, und was
dort steht, ist in der Prozessliste sichtbar. Auf einer Station, an der
nur der Betreiber arbeitet, ist ein Import **ohne** Passphrase deshalb
sauberer als eine, die im Klartext über die Kommandozeile geht. Wer
trotzdem eine setzt, trägt sie in der Operator-Verwaltung ein.

## Was die Appliance dann tut

Alle dreißig Minuten sammelt sie die noch nicht hochgeladenen
Verbindungen des Operators, schreibt sie als ADIF in eine temporäre
Datei und ruft auf:

```
xvfb-run -a tqsl -d -a compliant -l "<Station Location>" -u -x <datei>
```

Die vier Schalter decken die vier Stellen ab, an denen TQSL sonst einen
Dialog öffnen und unbeaufsichtigt hängenbleiben würde: Datumsbereich,
Duplikatbehandlung, Standortwahl und Passphrase.

Entscheidend ist die Auswertung des Ergebnisses. TQSL meldet nicht nur
Erfolg oder Misserfolg:

| Code | Bedeutung | Behandlung |
|---|---|---|
| 0 | alles signiert und hochgeladen | Erfolg |
| 8 | nichts übrig, alles waren Duplikate | Erfolg |
| 9 | teils Duplikate, Rest hochgeladen | Erfolg |
| 11 | LoTW nicht erreichbar | später erneut |
| 2, 4, 5, 6, 7, 10 | zurückgewiesen oder Fehler | Einrichtung prüfen |

Wer nur auf Null prüft, hält die Codes acht und neun für Fehler und lädt
dieselben Verbindungen endlos erneut hoch. Bei einem zweiten Lauf ist
acht der Normalfall.

Ein harter Fehler verbucht **nichts** als hochgeladen. Sonst wären die
Verbindungen still übersprungen, sobald jemand die Einrichtung richtet.

## Was nicht geprüft ist

Alles oben stammt aus der Dokumentation, nicht vom laufenden System: Es
gibt noch kein Zertifikat. Ungeprüft sind insbesondere das Verhalten von
`xvfb-run` bei langen Läufen, die genaue Form der Statuszeile in Version
2.8.1 und ob eine Station Location sich ohne echte grafische Oberfläche
anlegen lässt. Beim ersten echten Lauf gehört das Journal gelesen.
