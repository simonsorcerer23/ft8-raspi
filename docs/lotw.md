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

Was noch fehlt, sind zwei Schritte, die nur der Betreiber machen kann:

1. **Zertifikat beantragen.** Das läuft über TQSL selbst und dauert
   einige Tage. Die ARRL will eine Kopie der Zulassung und ein zweites
   Dokument mit Name und Adresse, per E-Mail an `LoTW-help@arrl.org`.
   Ein Postweg ist nicht nötig. Das Zertifikat gilt drei Jahre; die
   Erneuerung verlangt die Unterlagen nicht erneut.
2. **Zertifikat und Station Location auf dem Pi anlegen.** Beides
   braucht die grafische Oberfläche, also per `ssh -X ft8-pi5` mit
   weitergereichtem Display:

```bash
ssh -X ft8-pi5 'tqsl -i /pfad/zum/zertifikat.p12'
```

Danach die Station Location anlegen und benennen:

```bash
ssh -X ft8-pi5 'tqsl -s'
```

Der dort vergebene Name gehört anschließend in die Operator-Verwaltung,
Feld „LoTW-Station-Location". Ohne ihn signiert TQSL nicht, und der
Upload-Loop startet gar nicht erst.

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
