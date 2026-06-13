# Changelog

Alle nennenswerten Änderungen dieses Projekts. Generiert aus den
git-Tags via `scripts/gen_changelog.sh` (Quelle: Commit-Messages).

## v0.66.0 — 2026-06-13
- feat: add band mode autopilot

## v0.65.9 — 2026-06-12
- deploy: render install paths from environment
- docs: update hunt picker documentation

## v0.65.8 — 2026-06-12
- Improve hunt picker heuristics

## v0.65.7 — 2026-06-11
- fix: kein FT4-Log-Spam mehr — "short by samples" nur bei echtem Signalverlust

## v0.65.6 — 2026-06-11
- docs: FT8↔FT4 wechselt live (Restart-Limitation entfernt)
- fix: FT8↔FT4-Mode-Wechsel ohne Service-Neustart (Live-Slot-Retune)

## v0.65.5 — 2026-06-07
- fix: kein "Auto-Modus inaktiv"-Fehlalarm beim Self-Update-Restart

## v0.65.4 — 2026-06-07
- fix: ClubLog-Pending in Tageszusammenfassung nur für Operatoren mit Key

## v0.65.3 — 2026-06-04
- tune: psk_heard_us-Uprank zurückgenommen (größeres Sample widerlegt ihn)
- docs: GitHub-Außendarstellung auf Stand bringen (i18n, Telemetrie-Tuning, tz-Fix)

## v0.65.2 — 2026-06-03
- tune: tail_end_target unter snr (Telemetrie — als Entscheider nur ~3% Completion)

## v0.65.1 — 2026-06-03
- tune: hunt_priority — psk_heard_us hoch, tail_end_target runter (datengetrieben)

## v0.65.0 — 2026-06-03
- fix+audit: restliche tz-naive/aware-Bugs + Observability gegen stille Upload-Tode

## v0.64.4 — 2026-06-03
- fix: QRZ-/ClubLog-Uploads tot — tz-naive/aware-Crash im Drain-Loop

## v0.64.3 — 2026-06-02
- fix: PSK-Reciprocity-Loop liest Client pro Zyklus (Config-Save ohne Restart)

## v0.64.2 — 2026-06-02
- fix: DX-Cluster-Reader-Leak bei Config-Save (gleiche Bugklasse wie Blitzortung)

## v0.64.1 — 2026-06-02
- fix: Gewitter-Radius (und andere Blitzortung-Settings) per Hot-Reload wirksam

## v0.64.0 — 2026-06-01
- feat: Pick-Telemetrie komplett — winning_tier, PSK-SNR, Distanz & mehr

## v0.63.0 — 2026-06-01
- feat: Pick-Telemetrie erweitern — alles JETZT sammeln statt in einer Woche

## v0.62.0 — 2026-06-01
- feat: Pick-Latenz-Telemetrie — went_silent-Ursache instrumentieren

## v0.61.0 — 2026-06-01
- feat: 3. Frontend-i18n-Gate — hartcodiertes Deutsch ohne t() erkennen

## v0.60.0 — 2026-06-01
- fix: i18n-Audit-Nachzügler — restliche hartcodierte DE-Strings übersetzt
- ci: i18n-Audits als permanente Gates (Backend + Frontend)

## v0.59.1 — 2026-06-01
- fix: i18n-Audit — 5 hartkodierte deutsche UI-Strings nachgezogen

## v0.59.0 — 2026-06-01
- feat: Backend-i18n Welle B — alle ntfy-Push-Texte zweisprachig

## v0.58.0 — 2026-06-01
- feat: Backend-i18n Welle A — TX-Lock-Gründe + State-Hints zweisprachig
- docs: architecture.md englisch-primär + deutscher Mirror (architecture.de.md)

## v0.57.0 — 2026-06-01
- feat: i18n Welle 10 — restliche Panel-Strings (Residual-Cleanup)

## v0.56.0 — 2026-06-01
- feat: i18n Welle 9 — ConfigPanel, OperatorSwitcher, SystemUpdateCard, SolarWidget

## v0.55.2 — 2026-06-01
- feat: i18n Welle 8 — StateIndicator, SystemUpdateCard, SolarWidget

## v0.55.1 — 2026-06-01
- fix: DecodeList t() ohne i18n-Import (Laufzeit-Crash) + Gate dagegen

## v0.55.0 — 2026-06-01
- feat: i18n Welle 7 — WifiManager, OperatorAdminPanel, Map

## v0.54.0 — 2026-06-01
- feat: i18n Welle 6 — DecodeList, Charts, AccessPanel, FirstBootWizard

## v0.53.0 — 2026-06-01
- feat: i18n Welle 5 — ADIFTable-Filter + Kontinent-Namen (cont.*)

## v0.52.0 — 2026-06-01
- feat: i18n Welle 4 — 9 Komponenten (Panels, Charts, Stats, RigPanel)

## v0.51.0 — 2026-06-01
- feat: i18n Welle 3 — LoginGate, QsoConversation, OperatingLocationCard, DemoModeToggle

## v0.50.0 — 2026-06-01
- feat: i18n Welle 2 (StatusBar + ControlPanel) + zweisprachige README (DE/EN)

## v0.49.0 — 2026-06-01
- feat: i18n-Fundament (DE/EN) + Welle 1 (Header/Tabs/Footer) + Sprach-Umschalter
- docs: Screenshot-Galerie auf Englisch (README ist sonst eh EN + DE-Kurzfassung)
- docs: Screenshot-Galerie erweitert (DX-Jagd, Empfaenger, Konfiguration)

## v0.48.3 — 2026-06-01
- fix: 'Empfaenger' (PSK who-heard-me) zeigt im Demo die geseedeten DB-Zeilen
- feat: seed_demo_data fuellt auch Watchlist/Blacklist/Reputation/DXpedition/Empfaenger
- docs: Screenshot-Galerie (Demo-Modus) in README + architecture.md

## v0.48.2 — 2026-06-01
- fix: ntfy-Titel zeigt echten Hostnamen + Demo-Box schickt keine Mode-Alerts
- fix: seed_demo_data garantiert ~6 QSOs von heute (Dashboard 'QSOs heute')

## v0.48.1 — 2026-06-01
- feat: Demo-Modus ueberzeugend — Sim-Rig + Health-Demo-Awareness + frischere Seeds

## v0.48.0 — 2026-06-01
- feat: Demo-Modus per Konfig-Button umschaltbar

## v0.47.2 — 2026-06-01
- fix: seed_demo_data.py initialisiert DB-Engine (init_engine) vor session_scope

## v0.47.1 — 2026-06-01
- feat: demo_mode deaktiviert Uploads + Seed-Skript fuer Demo-QSOs

## v0.47.0 — 2026-06-01
- feat: Demo-Modus (Band-Simulator) fuer Onboarding & Doku-Screenshots

## v0.46.0 — 2026-05-31
- chore: Audit-Welle 2 — Exception-Sichtbarkeit, ruff-Gate, Mock-spec, Suite gruen

## v0.45.0 — 2026-05-31
- feat: Log-Filter fuer DXCC-Land, Kontinent & Marinefunker

## v0.44.1 — 2026-05-31
- fix: leere Panels (Live-Konversation/Solar/Map) — rohes fetch umging Token-Auth

## v0.44.0 — 2026-05-31
- feat: Headless-Autonomie statt DXCC-Spot-Notifications

## v0.43.0 — 2026-05-31
- fix: DX-Cluster-DXCC-Spots — Empty-Log-Guard gegen Fehlalarme

## v0.42.0 — 2026-05-31
- fix: 3 weitere 'existiert-gar-nicht'-Bugs + mypy-Typ-Gate gegen die Klasse

## v0.41.0 — 2026-05-31
- fix: ntfy-Pushes komplett kaputt (.push existiert nicht, heisst .notify)
- architecture.md auf Ist-Stand gebracht (war Planungsdokument)
- README: fehlende Features ergänzt (Auth, CEPT/GPS, Daten-Sicherheit, Changelog)
- Changelog: CHANGELOG.md (rückwirkend) + Auto-Generierung im Release

## v0.40.0 — 2026-05-30
- DATA-H1/H2: QRZ/ClubLog Upload-Klassifizierung — kein stiller Upload-Verlust

## v0.39.0 — 2026-05-30
- Auth-UX: merkbares Login-Passwort statt Zufalls-Token

## v0.38.0 — 2026-05-30
- Welle 2: Telemetrie-Retention + DB-Backup + nmcli-Guard + Write-Lock

## v0.37.0 — 2026-05-30
- SEC-C1: API-Token-Authentifizierung (Welle 3)

## v0.36.0 — 2026-05-30
- DATA-C1: QSO-Log bruchsicher — kein stiller Verlust mehr

## v0.35.0 — 2026-05-30
- Security/Daten-Haertung Welle 1 (Audit 2026-05-30)

## v0.34.0 — 2026-05-30
- Haertung: alle Config-Schreibpfade atomar + Guard auf alle Secrets

## v0.33.0 — 2026-05-30
- FIX (Datenverlust): config-save plaettet Operatoren/Credentials nicht mehr

## v0.32.0 — 2026-05-30
- QSO-Cooldown band-bewusst: blockt nicht mehr neue Band-Slots

## v0.31.0 — 2026-05-30
- Pick-Telemetrie: was_worked, was_new_dxcc, n_decodes, bail_reason

## v0.30.0 — 2026-05-30
- Pick-Attempt-Telemetrie für psk_heard_us-A/B (Vorwärts-Experiment)

## v0.29.3 — 2026-05-30
- DT-Drift-ntfy entfernt — Audio-Latenz ist nicht handlungsrelevant

## v0.29.2 — 2026-05-29
- UI: TX-Callsign prominent + API-Key-Verwaltung auf Konfig-Seite

## v0.29.1 — 2026-05-29
- Pre-Flight: ClubLog clublog_user=false ist kein "nicht angelegt"-Alarm

## v0.29.0 — 2026-05-29
- Operator-Modell: 2 Personen, Prä-/Suffixe als Sende-Calls darunter

## v0.28.3 — 2026-05-29
- Pre-Flight-Warnung auch beim DX-Location-Wechsel

## v0.28.2 — 2026-05-29
- ntfy-Topic pro Person statt pro On-Air-Call

## v0.28.1 — 2026-05-29
- Pre-Flight: QRZ-Logbuch-Callsign-Mismatch erkennen

## v0.28.0 — 2026-05-29
- Operator-Identitaet: Picker-Daten teilen + Pre-Flight-Setup-Check

## v0.27.0 — 2026-05-29
- Reputation: global statt pro-Operator + Basis-Call-Normalisierung

## v0.26.0 — 2026-05-29
- GPS-Country-Detection: Point-in-Polygon statt grober Rechtecke

## v0.25.0 — 2026-05-29
- CEPT-Laenderliste haarfein gegen DARC-Primaerquelle verifiziert

## v0.24.1 — 2026-05-29
- v0.24.1: USA fuer Klasse E freigeschaltet (CEPT-Novice korrekt)

## v0.24.0 — 2026-05-29
- v0.24.0: komplette CEPT-Laenderliste A+E + Klasse-E-CEPT-Fix

## v0.23.3 — 2026-05-29
- v0.23.3: Blitzortung-WS Reconnect mit Exp-Backoff

## v0.23.2 — 2026-05-29
- v0.23.2: PSK-Reporter Rate-Limit-Schonung (Ban-Praevention)

## v0.23.1 — 2026-05-29
- v0.23.1: Funkstille-Watchdog mit Audio-Liveness-Diskriminator

## v0.23.0 — 2026-05-29
- v0.23.0: konsequent UTC ueberall + PSK-Robustheit

## v0.22.0 — 2026-05-28
- v0.22.0: DX-Operating-Location mit GPS-Detection + CEPT-Compliance

## v0.21.5 — 2026-05-28
- v0.21.5: TZ-aware ISO-Timestamps für API-Responses

## v0.21.4 — 2026-05-28
- clublog: putlogs.php-Bulk-Upload statt realtime-Spam (v0.21.4)

## v0.21.3 — 2026-05-28
- clublog: Response-Matcher um Live-Varianten erweitert (v0.21.3)
- public-prep: LICENSE (MIT) + CREDITS.md + README internationalisiert
- security: app-password aus clublog.py docstring entfernt

## v0.21.2 — 2026-05-28
- v0.21.2: ADIF-Export mit ?operator= Filter + dynamischer Filename

## v0.21.1 — 2026-05-28
- v0.21.1: ClubLog api_key als drittes Credential-Feld

## v0.21.0 — 2026-05-28
- v0.21.0: ClubLog Real-Time-Upload-Integration

## v0.20.4 — 2026-05-27
- v0.20.4: Hard-Filter im Picker fuer die drei Filter-Tiers

## v0.20.3 — 2026-05-27
- v0.20.3: Pile-Up-Badge inline + ActiveHoursChart-Kachel

## v0.20.2 — 2026-05-27
- v0.20.2: freq-rep trackt Hunt-Picks + 3 neue Read-API-Endpunkte

## v0.20.1 — 2026-05-27
- v0.20.1: Hint-Texte-Sweep durchs ganze Frontend

## v0.20.0 — 2026-05-27
- v0.20.0: UI-Hints raus + Directed-CQ aufs Funk-Dashboard

## v0.19.2 — 2026-05-27
- v0.19.2: NG3K-Push-Throttle + Rarity-Gate (Anti-Spam)

## v0.19.1 — 2026-05-27
- v0.19.1: NG3K Auto-Import fuer DXpedition-Schedule

## v0.19.0 — 2026-05-27
- v0.19.0: Pile-Up-Avoidance + DXpedition-Schedule-Manager

## v0.18.0 — 2026-05-27
- v0.18.0: TX-Audio-Freq Smart-Hop + Frequency-Reputation

## v0.17.0 — 2026-05-27
- v0.17.0: Buddy-Seen + Adaptive-Cooldown + Watchlist-Hint

## v0.16.1 — 2026-05-27
- v0.16.1: AP-Fallback Save-Bug fix + Reputation-Panel im UI

## v0.16.0 — 2026-05-27
- v0.16.0: Hour-of-Day-Predictor + Tail-End-PreStage

## v0.15.0 — 2026-05-27
- v0.15.0: Soft-Blacklist (Bail-Reason-aware) + Slot-Parity-Predictor

## v0.14.0 — 2026-05-27
- v0.14.0: Watchlist + Grayline + Band-Open Tier

## v0.13.4 — 2026-05-27
- blitzortung: ws3 aus der Host-Rotation raus

## v0.13.3 — 2026-05-27
- ui: Antennen-Hint auch raus

## v0.13.2 — 2026-05-27
- ui: Online-Dienste entzerren + Hint-Texte raus

## v0.13.1 — 2026-05-27
- self-update: --no-block fix + sudoers + Blitzortung-Hint raus

## v0.13.0 — 2026-05-27
- blitzortung: 🌩️ Live-WS + ntfy-Storm-Warnung + Layout-Fix Auto-ALC

## v0.12.0 — 2026-05-27
- tail-end-button: 🎯 manueller Tail-End-Pickup in DecodeList

## v0.11.1 — 2026-05-27
- tail-end-hunter: 24h-Cooldown auch im synthetic-injection-Pfad respektieren

## v0.11.0 — 2026-05-26
- tail-end-hunter: 🎯 neuer Picker-Tier für RR73-Tail-End-Pickup

## v0.10.5 — 2026-05-26
- psk-badge: 📡 PSK-Reciprocity-Marker in DecodeList

## v0.10.4 — 2026-05-26
- psk-reciprocity: refresh-loop attribute-fix + diagnostic logging (v0.10.4)

## v0.10.3 — 2026-05-26
- hunt-priority: auto-migration für fehlende Tier-Namen (v0.10.3)

## v0.10.2 — 2026-05-26
- hunt-priority + ui-fixes (v0.10.2)

## v0.10.1 — 2026-05-26
- ui: operating section refactor — toggle-switches + sub-gruppen + konsistente inputs

## v0.10.0 — 2026-05-26
- hunt-priority: 9-Tier kaskadierender Picker + PSK-Reciprocity + 5BWAS + DXCC-Rarity

## v0.9.2 — 2026-05-26
- mf: eigene Operator-Calls aus Badge-Detection ausschliessen

## v0.9.1 — 2026-05-26
- fix: mf_lookup-import-Pfad in web/routes/ (3 dots statt 2)

## v0.9.0 — 2026-05-26
- Marinefunker-Mitgliederlookup ⚓ + Badge in Decodes/Log/ntfy

## v0.8.2 — 2026-05-26
- UI: Reboot-Button neben Shutdown + sudoers-Erweiterung

## v0.8.1 — 2026-05-25
- v0.8.1: Conversation-View strict filter (Bug-Fix)
- docs: decoder_evolution.md + architecture Decision-Journal Updates

## v0.8.0 — 2026-05-25
- v0.8.0: Decoder-Telemetrie + Self-Tuning — A+B+C+D+H+I

## v0.7.1 — 2026-05-25
- v0.7.1: Default decoder_mode = "extreme"

## v0.7.0 — 2026-05-25
- v0.7.0: Pi-5-Power — Subtract-and-Rerun + Hint-Decoder + Auto-Notch

## v0.6.4 — 2026-05-25
- v0.6.4: Compound-Call-TX-Bug — angle-brackets aus Decoded-Calls strippen

## v0.6.3 — 2026-05-25
- v0.6.3: decoder_mode + actual_decoder_mode in /api/status sichtbar

## v0.6.2 — 2026-05-25
- v0.6.2: Permission-Audit Findings — atomic config-write + auto-bak

## v0.6.1 — 2026-05-25
- v0.6.1: multi-default + YAML-magic-bool fix + DT-Push neutralisiert

## v0.6.0 — 2026-05-25
- v0.6.0: Anti-WSJT-X-Audit — alle 6 technischen Builds

## v0.5.4 — 2026-05-25
- v0.5.4: DT-Filter im Hunting-Picker (Audit-Lücke 1 vs WSJT-X)

## v0.5.3 — 2026-05-25
- v0.5.3: 'Naechste Aktion'-Hint vollstaendig — QSO_GRACE + auto_cq-Branch

## v0.5.2 — 2026-05-25
- v0.5.2: 3 Bugfixes — Funkstille-Spam, Tamper-Race, Conversation-IDLE-RX

## v0.5.1 — 2026-05-24
- band_hint dynamic: Live aus rig.freq_hz statt hartkodiertem bands[0] (v0.5.1)

## v0.5.0 — 2026-05-24
- Hashed-Call-Resolver: <...> in Decodes wird aufgeloest (v0.5.0)

## v0.4.6 — 2026-05-24
- QSO-Log + ntfy: Mode (FT8/FT4) sichtbar + filterbar (v0.4.6)

## v0.4.5 — 2026-05-24
- safety-floor: skip wenn Band fuer Klasse nicht erlaubt (v0.4.5)

## v0.4.4 — 2026-05-24
- UI + Self-Update + YAML-Fix-Bundle (v0.4.4)

## v0.4.3 — 2026-05-24
- FT4-Bugfix-Bundle: dial-switch defensiv + tamper + band-detect (v0.4.3)

## v0.4.2 — 2026-05-24
- FT4-Sub-Band-Support: Dial-Freq folgt Mode + Mode korrekt im QSO-Log (v0.4.2)

## v0.4.1 — 2026-05-24
- fix: Svelte 5 @const-in-markup invalid, inline expressions stattdessen
- RigPanel: Mode-Tag + Directed-CQ-Tag jetzt funktional (v0.4.1)

## v0.4.0 — 2026-05-24
- FT4-Mode voll unterstuetzt + UI-Toggle (Audit F6 v0.4.0)

## v0.3.4 — 2026-05-24
- FT8-Audit-Bundle F5+F7+F8+F9: 4 Findings erledigt (v0.3.4)

## v0.3.3 — 2026-05-24
- FT8-Komplettaudit: 4 Fixes + 6 dokumentierte Findings (v0.3.3)

## v0.3.2 — 2026-05-24
- WSJT-X-konformer R-Report: R + SNR-of-them-at-us (Audit Action 5)

## v0.3.1 — 2026-05-24
- fix(qso-log): rst_sent capture im Hunt-Pfad + Call-Spalte breiter
- docs/flags: cty.dat-Deployment-Hinweis (Runtime-Data, gitignored)

## v0.3.0 — 2026-05-24
- flags: DXCC-Country-Flag-Emojis in UI/Log/Decodes/ntfy

## v0.2.3 — 2026-05-24
- tx-power: Safety-Floor bei Reset-Events (Variante B clamp-down-only)

## v0.2.2 — 2026-05-24
- QSO_REPORT: picked_another-Bail (Audit Action 4)

## v0.2.1 — 2026-05-24
- QSO_REPORT: R-Resend auch bei 'Partner fällt zurück zu CQ' + docs

## v0.2.0 — 2026-05-24
- watchdog: sd_notify wired (READY + 10s heartbeat) + Type=notify
- docs/self_update: vollständige Aktualisierung + Watchdog-Rebuild-Plan

## v0.1.4 — 2026-05-24
- ft8-controller: WatchdogSec entfernt (war nie wired)

## v0.1.3 — 2026-05-24
- ft8-controller.service: Description ohne Hochgericht (Restbestand)

## v0.1.2 — 2026-05-24
- self-update: panic-before-restart + unit-file/sudoers sync

## v0.1.1 — 2026-05-24
- self-update.sh: Health-Probe nutzt /api/system/version (200 OK)

## v0.1.0 — 2026-05-24
- release.sh: cd in subshell, sonst persistiert es zwischen run-Calls
- ntfy: 'Auto-Modus inaktiv' + rig-check + Hochgericht→hostname
- Frontend: hostname im Titel; cffi-Build-Race fix in install + self-update
- Add tag-based self-update for both Pis + UI trigger
- Initial import: hochgericht-server-setup/ft8 → ft8-raspi standalone

