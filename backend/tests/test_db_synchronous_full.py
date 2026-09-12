"""synchronous=FULL statt NORMAL.

NORMAL war fuer SD-Karten gewaehlt; der Pi bootet seit dem 09.09.2026 von
NVMe. Unter WAL heisst NORMAL: Nach einem Stromausfall koennen die letzten
committeten Transaktionen fehlen. Nachgestellt am 12.09. mit kill -9 — der
Prozessabsturz allein verliert nichts, den OS-Cache deckt nur FULL ab.
"""
import pytest
from sqlalchemy import text
from ft8_appliance.db import session_scope
from ft8_appliance.db.session import create_all, init_engine


@pytest.mark.asyncio
async def test_datei_db_faehrt_synchronous_full(tmp_path):
    init_engine(tmp_path / "qso.sqlite")
    await create_all(default_user_callsign="DK9XR")
    async with session_scope() as s:
        wert = (await s.execute(text("PRAGMA synchronous"))).scalar()
        modus = (await s.execute(text("PRAGMA journal_mode"))).scalar()
    assert wert == 2, f"synchronous={wert} (2=FULL, 1=NORMAL)"
    assert str(modus).lower() == "wal"
