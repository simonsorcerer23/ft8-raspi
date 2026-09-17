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
Aus dem QRZ-Logbuch holt man sie über **Settings → ADIF Import/Export →
Export** (abopflichtig, und zwar für den Eigentümer des Logbuchs).

**Die Datei muss in Häppchen zerlegt werden.** Am 15.09.2026 mit 8605
Datensätzen durchgespielt: Ein einzelner Aufruf über die ganze Datei
blieb nach dem Versionsbanner stumm stehen und wurde nach fünfzehn
Minuten abgebrochen, ohne dass TQSL überhaupt eine Verbindung aufgebaut
hätte — nachprüfbar in `~/.tqsl/curl.log`, das in dieser Zeit keinen
einzigen `POST /lotw/upload` verzeichnete. In Portionen zu 500 lief
dieselbe Datei danach ohne Zwischenfall durch, achtzehn Aufrufe, jeder
`Success(0)`. Das ist dieselbe Chargengröße, die der Loop im Betrieb
verwendet.

**Der Umweg über eine signierte `.tq8` funktioniert nicht.** `tqsl -o`
erzeugt sie anstandslos, aber sie anschließend mit `tqsl -u` zu
verschicken hängt genauso stumm. Signieren und Hochladen gehören in
einen Aufruf, direkt aus der ADIF.

Wichtig ist außerdem `-a all` statt `-a compliant`: TQSL merkt sich in
`~/.tqsl/uploaded.db`, was es schon einmal signiert hat, und überspringt
solche Datensätze sonst wortlos. Bereits bei LoTW liegende QSOs sind
kein Problem — die weist der Server als Duplikate ab, das ist Status 8
oder 9.

```bash
# Auf dem Pi, je Häppchen:
xvfb-run -a tqsl -d -a all -l Weissenhorn -u -x /tmp/haeppchen/teil_001.adi
```

Erwartete Ausgabe am Ende: `Attempting to upload 500 QSOs`, dann
`Log uploaded successfully` und `Final Status: Success(0)`. Bleibt ein
Aufruf stumm, ist `~/.tqsl/curl.log` die Stelle, an der sich ablesen
lässt, ob überhaupt etwas gesendet wurde.

## Mehrere Sende-Rufzeichen

Eine Station Location trägt genau ein DXCC-Gebiet und einen Grid. QSOs
unter einem abweichenden On-Air-Call gehören nicht dazu: `/MM` und `/AM`
haben überhaupt kein DXCC. Sie mit der Heimat-Location zu signieren wäre
eine falsche Aussage gegenüber LoTW, und die lässt sich praktisch nicht
zurücknehmen. LoTW verlangt außerdem ein eigenes Callsign-Zertifikat je
gesendeter Variante, für `/MM` und `/AM` mit DXCC „-NONE-"
(lotw.arrl.org/lotw-help/submitting-qsos).

Der Upload-Loop gruppiert deshalb seit v0.161.0 nach
`station_callsign` und sucht für jede Gruppe eine eigene Location:

- Der Heimat-Call nimmt `lotw_station_location`.
- Jeder andere On-Air-Call braucht einen ausdrücklichen Eintrag. **Einen
  Rückfall auf die Heimat-Location gibt es bewusst nicht.** Seit v0.168.0
  gilt dieselbe Regel auch für QRZ, Club Log und eQSL — alle vier Dienste
  führen jede Variante als eigenes Rufzeichen.
- Ohne Eintrag bleiben die QSOs liegen, ohne Versuchszähler. Journal und
  eine Push-Nachricht sagen einmal, welche Location fehlt; die Meldung
  übersteht Neustarts (`ohne_einrichtung.json` neben `filter_drops.json`). Sie zählen nicht
  gegen die Chargengrenze, sonst würden ein paar hundert liegengebliebene
  Datensätze jeden Durchgang füllen und nichts Neues käme mehr an die
  Reihe.

Angelegt wird die zusätzliche Location wie die erste mit
`ssh -X ft8-pi5 'tqsl -s'`, für `/MM` und `/AM` mit DXCC „NONE".
Hinterlegt wird sie in der Operator-Verwaltung in der Zeile des
jeweiligen Sende-Calls, oder über die Schnittstelle:

```bash
curl -X PUT localhost:8000/api/operators/DK9XR/lotw-location \
  -H 'Content-Type: application/json' \
  -d '{"on_air_call": "DK9XR/MM", "station_location": "Schiff"}'
```

## Was es gebracht hat

Ob LoTW etwas bringt, misst `scripts/qsl_stand.py`. Es fragt die
QRZ-Logbook-API nach Gesamtzahl, bestätigten Verbindungen und
DXCC-Gebieten und merkt sich auf Wunsch einen Vergleichspunkt:

```bash
./scripts/qsl_stand.py --merken    # vor dem LoTW-Import bei QRZ
./scripts/qsl_stand.py             # danach — die Differenz steht da
```

QRZ zählt als bestätigt ausschließlich den eigenen Abgleich (beide
Seiten haben dieselbe Verbindung unabhängig ins QRZ-Logbuch geschrieben)
und Bestätigungen, die **direkt aus LoTW importiert** wurden. eQSL,
Club Log und Papierkarten stehen dort nie drin. Der LoTW-Import bei QRZ
läuft nicht von selbst — er wird im QRZ-Logbuch unter *Settings → LoTW*
angestoßen, wobei nur der LoTW-Benutzername hinterlegt wird; das
Passwort fragt QRZ jedes Mal neu und speichert es nicht.

Ausgangsstand am 15.09.2026, unmittelbar nach dem Hochladen des Altlogs
und **vor** jedem LoTW-Import: 8608 Verbindungen, davon 5341 bestätigt
(62,0 %), 134 DXCC-Gebiete.

## Der andere Weg: DCL

Das DARC Community Logbook nimmt Bestätigungen aus **LoTW, Club Log und
eQSL** entgegen und ist damit die einzige Stelle, an der die drei
Systeme zusammenlaufen. Der LoTW-Import liegt dort unter *Logbuch →
LoTW-Import*; einzugeben sind Benutzername, Passwort (wird nicht
gespeichert) und ein Stichtag, ab dem neue Bestätigungen geholt werden.
Für den ersten Lauf muss weit genug zurückdatiert werden, und der kann
je nach Menge Stunden dauern.

Eine DARC-Mitgliedschaft ist dafür nicht nötig: Wer keine hat, kann sich
im DCL über einen gleichlautenden LoTW-Account registrieren.

Umgekehrt geht nichts. LoTW nimmt ausschließlich eigene, signierte QSOs
an und kennt keinen Import fremder Bestätigungen.
