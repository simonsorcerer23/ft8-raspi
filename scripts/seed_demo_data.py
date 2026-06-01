#!/usr/bin/env python3
"""Seedet das Appliance-Log mit REIN FIKTIVEN QSOs fuer Doku-Screenshots.

Zweck: damit Log/Stats/Map/„wer hoert mich" auf einer Demo-Box (z.B. ft8-2
ohne Rig) gefuellt aussehen — OHNE echte Dritt-Rufzeichen (DSGVO) und OHNE
je ein echtes Logbuch anzufassen.

Sicherheit (doppelt):
  * demo_mode deaktiviert die QRZ/ClubLog-Upload-Loops ohnehin (v0.47.x).
  * zusaetzlich werden alle Seed-QSOs mit qrz_uploaded=True UND
    clublog_uploaded=True markiert → die Drain-Loops wuerden sie selbst
    dann ueberspringen, wenn sie liefen.

Aufruf (auf der Demo-Box, im backend/-venv):
    .venv/bin/python ../scripts/seed_demo_data.py [CALLSIGN] [ANZAHL]
    --wipe   vorher alle vorhandenen QSOs dieses Operators loeschen

Calls sind fiktiv (XYZ/AAA-Beispielstil), ueber Kontinente verteilt, damit
die DXCC-/Kontinent-Filter im Screenshot etwas zeigen.
"""
from __future__ import annotations

import asyncio
import random
import sys
from datetime import UTC, datetime, timedelta

# Fiktive Gegenstationen: (call, grid). Bewusst Beispiel-Calls, kein echtes
# Individuum. Spannweite EU/AS/AF/NA/SA/OC fuer den Kontinent-Filter.
DEMO_QSO_CALLS = [
    ("DL1XYZ", "JO31"), ("DK2AAA", "JO40"), ("OE3XYZ", "JN78"),
    ("HB9AAA", "JN47"), ("F4XYZ", "JN13"), ("ON4AAA", "JO20"),
    ("PA2XYZ", "JO22"), ("G4AAA", "IO91"), ("SP5XYZ", "JO90"),
    ("EA7AAA", "IM98"), ("SM5XYZ", "JP82"), ("LA9AAA", "JP20"),
    ("I2XYZ", "JN45"), ("YO3AAA", "KN34"), ("SV1XYZ", "KM18"),
    ("OH2AAA", "KP20"), ("JA1XYZ", "PM95"), ("UA9AAA", "MO04"),
    ("ZS6XYZ", "KG44"), ("K3AAA", "FN31"), ("W6XYZ", "DM04"),
    ("VE3AAA", "FN03"), ("PY2XYZ", "GG66"), ("LU4AAA", "GF05"),
    ("VK2XYZ", "QF56"), ("ZL2AAA", "RF80"),
]

# FT8-Dial-Frequenzen pro Band.
BAND_FREQ = {
    "40m": 7_074_000, "30m": 10_136_000, "20m": 14_074_000,
    "17m": 18_100_000, "15m": 21_074_000, "12m": 24_915_000, "10m": 28_074_000,
}
BANDS = list(BAND_FREQ)


async def main(callsign: str, count: int, wipe: bool) -> None:
    # Erst NACH dem Pfad-Setup importieren (Skript laeuft im backend/-venv).
    from ft8_appliance.db import session_scope
    from ft8_appliance.db.models import Qso
    from sqlalchemy import delete

    rng = random.Random(73)
    my_grid = "JO31"
    now = datetime.now(UTC)

    async with session_scope() as s:
        if wipe:
            await s.execute(delete(Qso).where(Qso.user_callsign == callsign))

        for i in range(count):
            call, grid = rng.choice(DEMO_QSO_CALLS)
            band = rng.choice(BANDS)
            # ueber die letzten ~45 Tage verteilen
            start = now - timedelta(
                days=rng.uniform(0, 45), minutes=rng.uniform(0, 1440)
            )
            s.add(Qso(
                call=call,
                band=band,
                freq_hz=BAND_FREQ[band] + rng.randint(200, 2500),
                mode=rng.choice(["FT8", "FT8", "FT8", "FT4"]),
                rst_sent=rng.randint(-18, 2),
                rst_rcvd=rng.randint(-18, 2),
                grid_rcvd=grid,
                qso_start=start,
                qso_end=start + timedelta(seconds=rng.randint(60, 150)),
                my_grid=my_grid,
                my_power_w=rng.choice([5, 10, 25, 50]),
                swr_avg=round(rng.uniform(1.0, 1.6), 1),
                station_callsign=callsign,
                user_callsign=callsign,
                # NIE hochladen — reine Demo-Daten:
                qrz_uploaded=True,
                clublog_uploaded=True,
                # ein paar mit MF-Nummer fuers ⚓-Badge im Screenshot
                mf_mfnr=rng.choice([None, None, None, 1234, 5678]),
            ))
        await s.commit()
    print(f"{count} fiktive Demo-QSOs fuer {callsign} eingefuegt"
          f"{' (nach Wipe)' if wipe else ''}.")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    cs = args[0] if args else "DK9XR"
    n = int(args[1]) if len(args) > 1 else 60
    asyncio.run(main(cs, n, "--wipe" in sys.argv))
