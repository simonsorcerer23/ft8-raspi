"""Kein Konfigurationsschalter darf ins Leere laufen.

Am 2026-09-11 stellte sich heraus, dass `alc_target_low` und
`alc_target_high` in der Bedienoberfläche angeboten und auf der Station
gesetzt sind — aber im Backend nirgends gelesen werden. Die ALC-Regelung
wurde am 2026-05-22 von einem Bang-Bang-Regler (Fenster [low, high]) auf
einen PI-Regler mit `alc_target_pct` umgestellt; die alten Felder blieben
stehen. Wer daran dreht, ändert nichts.

Dieselbe Fehlerklasse wie der Randfrequenz-Filter, die Cooldown-Verkürzung
und die Slot-Paritäts-Prüfung desselben Tages: eine Regel, deren
Begründung eine spätere Änderung nicht überlebt hat.

Der Test hält den Zustand fest. Wird ein Schalter absichtlich stillgelegt,
gehört er in ``VERALTET`` — mit dem Grund, damit niemand ihn stillschweigend
wiederbelebt oder aus der Oberfläche heraus daran dreht.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

WURZEL = pathlib.Path(__file__).resolve().parents[1] / "ft8_appliance"
MODELS = WURZEL / "config" / "models.py"

# Schalter, die bewusst nichts mehr tun. Sie bleiben im Modell, weil
# bestehende config.yaml sie gesetzt haben und AppConfig mit
# extra="forbid" laeuft — ein Entfernen wuerde die Station am Start
# scheitern lassen.
VERALTET = {
    "alc_target_low": "PI-Regler seit 2026-05-22, Sollwert ist alc_target_pct",
    "alc_target_high": "PI-Regler seit 2026-05-22, Sollwert ist alc_target_pct",
    "auto_cq_interval_s": "CQ_CALLING sendet an jeder paritaetspassenden Slot-Grenze",
    "answer_only_my_call": "nie implementiert, auch nicht in der Oberflaeche",
}


def _felder_von(klasse: str) -> list[str]:
    quelle = MODELS.read_text()
    blk = re.search(rf"class {klasse}.*?(?=\nclass )", quelle, re.S)
    assert blk, f"{klasse} nicht gefunden"
    felder = re.findall(r"^\s{4}([a-z_0-9]+)\s*:", blk.group(0), re.M)
    return [f for f in felder if not f.startswith("_") and f != "model_config"]


def _wird_gelesen(feld: str) -> bool:
    r = subprocess.run(
        ["grep", "-rl", feld, str(WURZEL), "--include=*.py"],
        capture_output=True, text=True,
    )
    return any("config/models.py" not in d for d in r.stdout.split())


def test_jeder_schalter_wird_irgendwo_gelesen():
    tot = [f for f in _felder_von("OperatingConfig")
           if not _wird_gelesen(f) and f not in VERALTET]
    assert not tot, (
        "Diese Schalter werden nirgends gelesen — entweder anschliessen oder "
        f"in VERALTET eintragen: {tot}"
    )


def test_veraltete_schalter_sind_im_modell_markiert():
    """Sonst dreht jemand daran und wundert sich, dass nichts passiert."""
    zeilen = MODELS.read_text().splitlines()
    ohne_hinweis = []
    for feld in VERALTET:
        idx = next((i for i, z in enumerate(zeilen)
                    if re.match(rf"\s{{4}}{feld}\s*:", z)), None)
        if idx is None:
            continue          # Feld ganz entfernt — auch in Ordnung
        davor = " ".join(zeilen[max(0, idx - 8):idx]).lower()
        if "veraltet" not in davor and "wirkungslos" not in davor:
            ohne_hinweis.append(feld)
    assert not ohne_hinweis, (
        f"veraltet, aber im Modell nicht als solches gekennzeichnet: {ohne_hinweis}"
    )


def test_veraltete_schalter_sind_nicht_einstellbar():
    """Was nichts tut, soll man auch nicht einstellen koennen.

    Geprueft wird auf Eingabefelder (``bind:value``), nicht auf jede
    Erwaehnung: Die YAML-Erzeugung schreibt die Werte weiterhin mit, damit
    sie beim Speichern aus der Oberflaeche nicht aus der Konfiguration
    fallen. Sichtbar einstellbar sollen sie aber nicht sein.
    """
    frontend = WURZEL.parents[1] / "frontend" / "src"
    if not frontend.exists():
        return
    einstellbar = []
    for feld in VERALTET:
        g = subprocess.run(
            ["grep", "-rn", f"bind:value={{cfg.operating.{feld}}}", str(frontend)],
            capture_output=True, text=True,
        )
        if g.stdout.strip():
            einstellbar.append(feld)
    assert not einstellbar, (
        f"veraltete Schalter noch in der Oberflaeche einstellbar: {einstellbar}"
    )
