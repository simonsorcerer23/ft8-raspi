"""Audit vom 14.11.2026, Abschnitt 7 (Oberflaeche): F3 bis F12.

Fuer die Oberflaeche gibt es keine eigene Testinfrastruktur. Was sich am
Quelltext festhalten laesst, steht hier; was das Backend liefert (Statistik
je Operator, ADIF-Dateiname, eQSL-QTH-Nickname), wird ueber die API geprueft.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ft8_appliance.config.models import _RIG_TABLE

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
KOMPONENTEN = FRONTEND / "components"

pytestmark = pytest.mark.skipif(
    not FRONTEND.is_dir(), reason="Frontend-Quellen nicht vorhanden",
)


# --- F3: eine Rig-Tabelle fuer alle Komponenten -----------------------------

def _frontend_rigs() -> dict[str, int]:
    txt = (FRONTEND / "lib" / "rigs.js").read_text(encoding="utf-8")
    block = txt[txt.index("export const RIGS = {"):txt.index("\n};")]
    return {
        m.group(1): int(m.group(2))
        for m in re.finditer(r"^\s{2}(\w+):\s*\{[^}]*?max_power_w:\s*(\d+)", block, re.M)
    }


def test_f3_rig_tabelle_deckt_sich_mit_dem_backend() -> None:
    """Jedes Modell des Backends steht mit derselben Werksleistung im Frontend.

    Am 14.11. fehlten ft817/ft818 in der Konfigseite: der Zugriff auf
    ``RIG_DEFAULTS[model].max_power_w`` warf und die Seite blieb leer.
    """
    backend = {name: profil.max_power_w for name, profil in _RIG_TABLE.items()}
    assert _frontend_rigs() == backend


def test_f3_keine_komponente_fuehrt_eine_eigene_rig_tabelle() -> None:
    eigene: list[str] = []
    for datei in KOMPONENTEN.glob("*.svelte"):
        txt = datei.read_text(encoding="utf-8")
        if re.search(r"\bic7300mk2\s*:", txt):
            eigene.append(datei.name)
    assert not eigene, f"eigene Rig-Tabelle statt lib/rigs.js: {eigene}"


# --- F4: QSO-Ton am Zaehler, QSO_GRACE in der Anzeige -----------------------

def test_f4_qso_ton_haengt_am_zaehler_nicht_an_der_zustandsfolge() -> None:
    """Ein geloggtes QSO laeuft ueber QSO_GRACE; "QSO_REPORT → IDLE" ist der
    Abbruch. Der Ton darf deshalb an keiner Zustandsfolge haengen."""
    txt = (FRONTEND / "lib" / "sound.svelte.js").read_text(encoding="utf-8")
    code = "\n".join(z for z in txt.splitlines() if not z.lstrip().startswith("//"))
    assert "'QSO_REPORT'" not in code and "'QSO_LOG'" not in code
    assert "qso_total" in code
    assert "toLocaleTimeString" not in code, "Zeiten im Funkbetrieb sind UTC"
    assert not re.search(r"notify\(\s*'", code), "Meldungstext nicht ueber t()"


def test_f4_jeder_zustand_hat_einen_namen_in_der_anzeige() -> None:
    """QSO_GRACE erschien roh in der Statusleiste."""
    from ft8_appliance.statemachine.states import State

    de = (FRONTEND / "lib" / "locales" / "de.js").read_text(encoding="utf-8")
    en = (FRONTEND / "lib" / "locales" / "en.js").read_text(encoding="utf-8")
    leiste = (KOMPONENTEN / "StatusBar.svelte").read_text(encoding="utf-8")
    banner = (KOMPONENTEN / "StateIndicator.svelte").read_text(encoding="utf-8")
    for state in State:
        assert f"'{state.name}'" in leiste, f"StatusBar kennt {state.name} nicht"
        assert f"t('stateind.{state.name}')" in banner, f"StateIndicator kennt {state.name} nicht"
        for katalog in (de, en):
            assert f"'statusbar.state.{state.name}'" in katalog
            assert f"'stateind.{state.name}'" in katalog


# --- F5: Verbindungsverlust und Badge-Grund sichtbar ------------------------

def test_f5_verbindungsverlust_wird_angezeigt() -> None:
    """statusStore.error und lastFetch las keine einzige Komponente."""
    banner = (KOMPONENTEN / "ConnectionBanner.svelte").read_text(encoding="utf-8")
    assert "statusStore.lastFetch" in banner and "statusStore.error" in banner
    assert "t('conn.lost_since'" in banner
    app = (FRONTEND / "App.svelte").read_text(encoding="utf-8")
    assert "<ConnectionBanner />" in app


def test_f5_badge_klick_zeigt_den_grund_statt_console_log() -> None:
    app = (FRONTEND / "App.svelte").read_text(encoding="utf-8")
    assert "console.log('badge detail'" not in app
    badges = (KOMPONENTEN / "StatusBadges.svelte").read_text(encoding="utf-8")
    assert "console.log" not in badges
    assert "reason(open, openSection)" in badges


# --- F7: Statistik und ADIF-Export je Operator ------------------------------

@pytest.fixture
def db():
    import asyncio

    from ft8_appliance.db.session import create_all, init_engine

    init_engine(":memory:")
    asyncio.run(create_all())
    yield


def _client(callsign: str = "DK9XR"):
    from fastapi.testclient import TestClient

    from ft8_appliance.web import create_app
    from tests.test_web import FakeOrchestrator

    class _Operator:
        pass

    class _Config:
        operator = _Operator()

    _Config.operator.callsign = callsign
    orch = FakeOrchestrator()
    orch.config = _Config()
    app = create_app()
    app.state.orchestrator = orch
    return TestClient(app, client=("127.0.0.1", 50000))


def _seed_zwei_operatoren() -> None:
    """Zwei QSOs von DK9XR, eines von DO3XR — alle von heute."""
    import asyncio
    from datetime import UTC, datetime, timedelta

    from ft8_appliance.db import session_scope
    from ft8_appliance.db.models import Qso

    async def seed() -> None:
        now = datetime.now(UTC)
        async with session_scope() as s:
            for i, (call, op) in enumerate(
                (("EA5KB", "DK9XR"), ("JA1XYZ", "DK9XR"), ("W1AW", "DO3XR"))
            ):
                s.add(Qso(
                    call=call, band="15m", freq_hz=21_074_000, mode="FT8",
                    rst_sent=-10, rst_rcvd=-12, grid_rcvd="IM99",
                    qso_start=now - timedelta(seconds=30 + i),
                    qso_end=now - timedelta(seconds=i),
                    my_grid="JN58", my_power_w=50, swr_avg=1.1,
                    notes=None, my_lat=None, my_lon=None, user_callsign=op,
                    clublog_uploaded=False,
                ))

    asyncio.run(seed())


def test_f7_statistik_zaehlt_nur_den_aktiven_operator(db) -> None:
    """ "QSOs heute" mischte DK9XR und DO3XR; das Log darunter filterte."""
    _seed_zwei_operatoren()

    dk = _client("DK9XR").get("/api/stats").json()
    assert dk["callsign"] == "DK9XR"
    assert (dk["qso_7d"], dk["qso_total"]) == (2, 2)

    do = _client("DO3XR").get("/api/stats").json()
    assert do["callsign"] == "DO3XR"
    assert (do["qso_7d"], do["qso_total"]) == (1, 1)


def test_f7_adif_dateiname_sagt_was_drin_ist(db) -> None:
    """Mit Filter das Rufzeichen, ohne Filter "alle" — nie der aktive
    Operator ueber einer Datei mit den QSOs aller Operatoren."""
    _seed_zwei_operatoren()
    client = _client("DK9XR")

    r = client.get("/api/log/adif?operator=do3xr")
    assert 'filename="do3xr_ft8.adif"' in r.headers["content-disposition"]
    assert "W1AW" in r.text and "EA5KB" not in r.text

    r = client.get("/api/log/adif")
    assert 'filename="alle_ft8.adif"' in r.headers["content-disposition"]
    assert "W1AW" in r.text and "EA5KB" in r.text


def test_f7_adif_dateiname_ohne_pfadzeichen(db) -> None:
    r = _client().get("/api/log/adif", params={"operator": '9a/dk9xr"x'})
    assert 'filename="9a_dk9xr_x_ft8.adif"' in r.headers["content-disposition"]


def test_f7_funk_tab_exportiert_mit_aktivem_rufzeichen() -> None:
    """Kein fester Dateiname und kein ungefilterter Export im Funk-Tab."""
    for name in ("StatsDashboard.svelte", "ADIFTable.svelte"):
        txt = (KOMPONENTEN / name).read_text(encoding="utf-8")
        assert "dk9xr_ft8.adif" not in txt, name
        assert "api.downloadAdif(activeCall)" in txt, name
    assert "api.adifUrl()" not in (KOMPONENTEN / "StatsDashboard.svelte").read_text(encoding="utf-8")


# --- F8: QSL-Grossansicht zeigt beim Blaettern das Bild der neuen Karte ------

def test_f8_qsl_bild_verwirft_das_bild_beim_wechsel_der_karte() -> None:
    """Die Komponente wird beim Blaettern wiederverwendet; der Lade-Effekt
    brach bei gesetzter url ab und liess das alte Bild stehen."""
    txt = (KOMPONENTEN / "QslBild.svelte").read_text(encoding="utf-8")
    # Ein Effekt haengt an der id und gibt das alte Bild frei ...
    assert re.search(r"\$effect\(\(\) => \{\s*id;\s*untrack\(\(\) => \{ freigeben\(\);", txt)
    # ... und eine verspaetete Antwort fuer die vorige Karte wird verworfen.
    assert "fuer !== id" in txt


# --- F9: eQSL-QTH-Nickname sehen und leeren ---------------------------------

def _operator_client():
    from fastapi.testclient import TestClient

    from ft8_appliance.config import AppConfig
    from ft8_appliance.web import create_app

    class _Orch:
        def __init__(self) -> None:
            self.config = AppConfig.model_validate({
                "operators": [{
                    "callsign": "DK9XR", "license_class": "A",
                    "eqsl_user": "DK9XR", "eqsl_password": "geheim",
                    "eqsl_qth_nickname": "Heimat",
                }],
                "active_callsign": "DK9XR",
                "antennas": [{"name": "dipol", "bands": ["15m"]}],
            })

        async def persist_config(self) -> None:
            pass

        def reload_active_operator_integrations(self) -> None:
            pass

    orch = _Orch()
    app = create_app()
    app.state.orchestrator = orch
    return TestClient(app, client=("127.0.0.1", 50000)), orch


def test_f9_eqsl_qth_nickname_wird_ausgeliefert_und_laesst_sich_leeren() -> None:
    client, orch = _operator_client()

    op = client.get("/api/operators").json()["operators"][0]
    assert op["eqsl_qth_nickname"] == "Heimat"
    assert "eqsl_password" not in op, "das Passwort bleibt geheim"

    r = client.patch("/api/operators/DK9XR", json={"eqsl_qth_nickname": ""})
    assert r.status_code == 200, r.text
    assert r.json()["eqsl_qth_nickname"] is None
    assert orch.config.operators[0].eqsl_qth_nickname is None
    assert orch.config.operators[0].eqsl_password == "geheim"


def test_f9_formular_startet_mit_dem_gespeicherten_nickname() -> None:
    txt = (KOMPONENTEN / "OperatorAdminPanel.svelte").read_text(encoding="utf-8")
    assert "eqsl_qth_nickname: op.eqsl_qth_nickname ?? ''" in txt


# --- F6: NR-Lampe am Schalter, ANF und Tuner sichtbar -----------------------

def test_f6_nr_lampe_haengt_am_schalter() -> None:
    """nr_level bleibt bei ausgeschaltetem NR auf dem letzten Wert stehen —
    die Lampe leuchtete, obwohl NR aus war."""
    txt = (KOMPONENTEN / "RigPanel.svelte").read_text(encoding="utf-8")
    assert "class:on={rig.nr_on === true}>NR<" in txt
    assert "rig.nr_level" not in txt
    assert "class:on={rig.anf_on}>ANF<" in txt
    assert "class:on={rig.tuner_on}>TUNER<" in txt


def test_f6_status_liefert_die_schalter() -> None:
    from ft8_appliance.web.routes.status import RigSnapshotOut

    assert {"nr_on", "anf_on", "tuner_on"} <= set(RigSnapshotOut.model_fields)


# --- F10: sichtbare Texte ueber t(), keine falsche Filterbreite -------------

@pytest.mark.parametrize(("datei", "roh"), [
    ("RigPanel.svelte", "weniger Sensitivity"),
    ("RigPanel.svelte", "Standard-Modus"),
    ("RigPanel.svelte", ">offline<"),
    ("RigPanel.svelte", "Instantaneous ALC"),
    ("RigPanel.svelte", "Audio-gain factor"),
    ("DecodeList.svelte", "Keine Decodes an dich"),
    ("QsoConversation.svelte", ">seit {"),
    ("QsoConversation.svelte", 'title="Marinefunker'),
    ("ADIFTable.svelte", "'Zeit (UTC)'"),
    ("ADIFTable.svelte", "'Frequenz'"),
    ("ADIFTable.svelte", "<h2>QSO-Log</h2>"),
    ("ADIFTable.svelte", "'alle'"),
    ("ADIFTable.svelte", 'title="nur Marinefunker'),
    ("SwrTrendChart.svelte", "'jetzt'"),
])
def test_f10_text_laeuft_ueber_den_katalog(datei: str, roh: str) -> None:
    txt = (KOMPONENTEN / datei).read_text(encoding="utf-8")
    assert roh not in txt, f"{datei}: {roh!r} steht hart im Quelltext"


def test_f10_keine_feste_filterbreite_im_text() -> None:
    """Die Icom-Profile stellen 3600 Hz ein, FT-817/818 gar keine Breite —
    "2700 Hz" im Knopf- und Schaltertext war fuer keines davon richtig."""
    from ft8_appliance.config.models import _RIG_TABLE

    assert {p.mode_width_hz for p in _RIG_TABLE.values()} != {2700}
    for lang in ("de", "en"):
        txt = (FRONTEND / "lib" / "locales" / f"{lang}.js").read_text(encoding="utf-8")
        for key in ("control.restore_rig", "cfg.rig_auto_restore"):
            zeile = next(z for z in txt.splitlines() if f"'{key}'" in z)
            assert "2700" not in zeile, zeile


# --- F11: DXpedition-Zeiten in UTC eingeben ---------------------------------

def test_f11_dxpedition_eingabe_wird_als_utc_gedeutet() -> None:
    """datetime-local liefert eine Zeit ohne Zone; new Date() las sie als
    Ortszeit, die Tabelle zeigte danach UTC — um 1 bis 2 Stunden verschoben."""
    txt = (KOMPONENTEN / "DxpeditionPanel.svelte").read_text(encoding="utf-8")
    assert "new Date(newStart)" not in txt and "new Date(newEnd)" not in txt
    assert "parseUtc(newStart)" in txt and "parseUtc(newEnd)" in txt
    assert "t('dxp.start_utc')" in txt and "t('dxp.end_utc')" in txt
    for roh in ("'vorbei'", "'QRV jetzt'", "'👀 ja'"):
        assert roh not in txt, f"{roh} steht hart im Quelltext"


# --- F12: Karte raeumt ihre Timer ab und escaped Fremdtext ------------------

def _karte() -> str:
    return (KOMPONENTEN / "Map.svelte").read_text(encoding="utf-8")


def test_f12_jeder_timer_der_karte_wird_geloescht() -> None:
    """setInterval(renderTerminator) lief nach dem Verlassen des Tabs weiter."""
    txt = _karte()
    assert not re.search(r"^\s*setInterval\(", txt, re.M), "Timer ohne Handle"
    namen = re.findall(r"const (\w+) = setInterval\(", txt)
    assert len(namen) >= 2
    for name in namen:
        assert f"clearInterval({name})" in txt, f"{name} wird nie geloescht"


def test_f12_fremdtext_in_popups_ist_escaped() -> None:
    """Rufzeichen, Decode-Texte und DX-Cluster-Felder gehen nur durch esc()
    in bindPopup/bindTooltip — Leaflet setzt den Inhalt als HTML."""
    txt = _karte()
    code = "\n".join(z for z in txt.splitlines() if not z.lstrip().startswith("//"))
    roh = re.findall(r"(?<!esc\()\$\{(?:sp|m|d|loc|b)\.[a-z_]+(?: \?\? '[^']*')?\}", code)
    # Erlaubt: Zahlen, die vor dem Einsetzen formatiert werden (toFixed).
    assert roh == [], f"ungefilterter Fremdtext im Popup: {roh}"
    for feld in ("sp.comment", "sp.country", "sp.spotter", "sp.spotted", "d.message", "m.call"):
        assert f"esc({feld}" in code, feld


def test_f12_esc_macht_html_unschaedlich() -> None:
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node nicht installiert")
    skript = (
        "import { esc } from './html.js';"
        "console.log(esc('<img src=x onerror=alert(1)> & \"q\" \\'s\\''));"
        "console.log('[' + esc(null) + '|' + esc(undefined) + '|' + esc(0) + ']');"
    )
    aus = subprocess.run(
        [node, "--input-type=module", "-e", skript],
        cwd=FRONTEND / "lib", capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert aus[0] == "&lt;img src=x onerror=alert(1)&gt; &amp; &quot;q&quot; &#39;s&#39;"
    assert aus[1] == "[||0]"
