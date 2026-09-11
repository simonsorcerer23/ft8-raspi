"""Der Entwickler-Stack darf nichts nach draussen melden.

Die Signale dieses Stacks stammen aus einem Simulator. Sie sind erfunden
und duerfen kein oeffentliches Meldenetz erreichen — was von dort nach
draussen ginge, traegt das Rufzeichen des Betreibers und waere von echten
Empfangsberichten nicht zu unterscheiden.

Die Gefahr liegt nicht in einem Fehler im Skript, sondern in einem
*fehlenden Eintrag*: ``PskReporterConfig`` hat ``enabled=True`` und
``upload_decodes=True`` als Vorgabe — richtig fuer die Station am
Antennenmast, falsch fuer jeden Testlauf. Wer die Konfiguration des
Skripts erweitert und den ``integrations``-Block vergisst, erbt diese
Vorgabe stillschweigend.

Der Test liest die Konfiguration aus dem Skript selbst, damit er nicht
bloss eine Kopie der Absicht prueft.
"""

from __future__ import annotations

import ast
from pathlib import Path

SKRIPT = Path(__file__).resolve().parents[2] / "scripts" / "dev_run.py"


def _integrations_literal() -> dict:
    """Das ``integrations=``-Argument aus dem AppConfig-Aufruf im Skript."""
    baum = ast.parse(SKRIPT.read_text())
    for knoten in ast.walk(baum):
        if not isinstance(knoten, ast.Call):
            continue
        if getattr(knoten.func, "id", None) != "AppConfig":
            continue
        for kw in knoten.keywords:
            if kw.arg == "integrations":
                return ast.literal_eval(kw.value)
    raise AssertionError("dev_run.py baut keine AppConfig mit integrations=")


def test_psk_reporter_ist_aus():
    psk = _integrations_literal().get("psk_reporter", {})
    assert psk.get("enabled") is False, (
        "dev_run.py wuerde Simulator-Decodes an pskreporter.info senden"
    )
    assert psk.get("upload_decodes") is False


def test_blitzortung_haelt_keine_dauerverbindung():
    assert _integrations_literal().get("blitzortung", {}).get("enabled") is False


def test_kein_dienst_der_nach_draussen_schreibt_ist_an():
    """Sammelklausel: was hier auftaucht, muss ausdruecklich aus sein."""
    integrationen = _integrations_literal()
    for name in ("psk_reporter", "qrz", "clublog", "ntfy", "dx_cluster"):
        eintrag = integrationen.get(name)
        if eintrag is None:
            continue          # nicht gesetzt -> Vorgabe; nur psk_reporter ist
            # per Vorgabe an, und den decken die Tests oben ab
        assert eintrag.get("enabled") is False, f"{name} sendet aus dem Dev-Stack"
