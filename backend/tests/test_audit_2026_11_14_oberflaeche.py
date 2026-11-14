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
