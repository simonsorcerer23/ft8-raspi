"""Ein Konfigurations-Neuladen darf keinen Manipulationsalarm ausloesen.

Am 2026-09-14 loeste das Speichern eines A/B-Schalters auf der Konfigseite
die Meldung "TX-Power sync: rig reports 70W (was 10W) — EXTERN verstellt"
samt Push aufs Handy aus. Niemand hatte am Geraet gedreht.

Die Ursache: Der Laufzeitwert der Sendeleistung ueberlebt Updates ueber
runtime_state (70 W), der Konfigurationswert stand seit der
Ersteinrichtung auf 10 W. Beim Neuladen verglich die Station den
Konfigurationswert mit dem LAUFZEITWERT statt mit der vorigen
Konfiguration — die Abweichung war der Normalfall, nicht eine Aenderung
durch den Benutzer. Sie setzte sich auf 10 W, sah am Rig 70 und schloss
auf einen Eingriff.
"""
from __future__ import annotations

import pathlib
import re

QUELLE = (pathlib.Path(__file__).resolve().parents[1] / "ft8_appliance"
          / "runtime" / "orchestrator.py").read_text()


def test_vergleich_gegen_die_vorige_konfiguration() -> None:
    """Nicht gegen den Laufzeitwert — der weicht im Normalbetrieb ab."""
    assert "vorige_default_power = self.config.operator.default_power_w" in QUELLE, \
        "der vorige Konfigurationswert wird nicht mehr gesichert"
    assert "if new_cfg.operator.default_power_w != vorige_default_power:" in QUELLE, \
        "Sendeleistung wird wieder gegen den Laufzeitwert verglichen"


def test_alter_vergleich_ist_weg() -> None:
    assert "default_power_w != self._tx_power_w" not in QUELLE, \
        "der alte Vergleich gegen den Laufzeitwert steht wieder da"


def test_der_wert_wird_vor_dem_ueberschreiben_gelesen() -> None:
    """self.config = new_cfg darf nicht davor stehen, sonst liest die
    Sicherung schon den neuen Wert und der Vergleich ist immer falsch."""
    i_sichern = QUELLE.index("vorige_default_power = self.config.operator.default_power_w")
    i_ueberschreiben = QUELLE.index("self.config = new_cfg")
    assert i_sichern < i_ueberschreiben, \
        "der vorige Wert wird erst nach dem Ueberschreiben gelesen"


def test_manipulationsalarm_existiert_weiter() -> None:
    """Der Alarm soll bleiben — nur nicht bei einem Neuladen feuern."""
    assert re.search(r"EXTERN verstellt", QUELLE), \
        "die Tamper-Erkennung ist ganz verschwunden"
