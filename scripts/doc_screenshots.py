#!/usr/bin/env python3
"""Screenshots der Oberflaeche fuer Doku und Website — reproduzierbar.

Startet Chrome headless gegen eine *lokale Demo-Instanz* (``dev_run.py``),
klickt die gewuenschten Ansichten an und legt PNGs ab. Damit muss niemand
von Hand knipsen, und nach einer UI-Aenderung sind die Bilder in einem Lauf
wieder aktuell.

    ./scripts/dev_run.py &                 # Demo-Stack auf :8000
    ./scripts/doc_screenshots.py ../dk9xr-web/static/ft8

Wichtig: laeuft bewusst nur gegen 127.0.0.1. Die echte Station wird nicht
angefasst — dort stehen fremde Rufzeichen und echte Logdaten drin.

Chrome wird ueber das DevTools-Protokoll gesteuert (kein Playwright noetig,
das Repo soll dafuer keine Extra-Abhaengigkeit bekommen).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

import websockets

BASIS = "http://127.0.0.1:8000"

# (Dateiname, Tab-Beschriftung, Anker zum Scrollen | None, Hoehe)
#
# Bewusst NICHT dabei: der Empfaenger-Tab. Er fragt live bei pskreporter.info
# ab und zeigt damit echte Rufzeichen Dritter — dieselbe Linie, die
# seed_demo_data.py zieht (fiktive Calls statt echter). Alles andere hier
# stammt aus dem Simulator bzw. dem Demo-Seed.
ANSICHTEN: list[tuple[str, str, str | None, int]] = [
    ("ui-funk.png",      "Funk",   None,               1500),
    ("ui-auswahl.png",   "Konfig", "Hunt-Priorität",   1330),
    ("ui-strategie.png", "Konfig", "Antwortstrategie", 400),
    ("ui-karte.png",     "Karte",  None,               1100),
    ("ui-log.png",       "Log",    None,               1150),
]

BREITE = 1000
# Die Kopf- und Tableiste steht fest oben; ohne Versatz rutscht die
# angesteuerte Ueberschrift darunter und fehlt im Bild.
KOPFLEISTE_PX = 130


def freier_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def chrome_binary() -> str:
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if (pfad := shutil.which(name)):
            return pfad
    sys.exit("FATAL: kein Chrome/Chromium gefunden")


class Devtools:
    """Duenne CDP-Huelle: nur was fuer Screenshots gebraucht wird."""

    def __init__(self, ws) -> None:
        self.ws = ws
        self._id = 0

    async def ruf(self, methode: str, **params):
        self._id += 1
        eigene = self._id
        await self.ws.send(json.dumps({"id": eigene, "method": methode, "params": params}))
        while True:
            antwort = json.loads(await self.ws.recv())
            if antwort.get("id") == eigene:
                if "error" in antwort:
                    raise RuntimeError(f"{methode}: {antwort['error']}")
                return antwort.get("result", {})

    async def js(self, ausdruck: str):
        res = await self.ruf(
            "Runtime.evaluate", expression=ausdruck,
            awaitPromise=True, returnByValue=True,
        )
        return res.get("result", {}).get("value")


async def hole_bilder(ziel: Path) -> int:
    port = freier_port()
    profil = tempfile.mkdtemp(prefix="ft8-shots-")
    proc = subprocess.Popen(
        [
            chrome_binary(), "--headless=new", "--disable-gpu", "--hide-scrollbars",
            f"--remote-debugging-port={port}", f"--user-data-dir={profil}",
            f"--window-size={BREITE},1200", "about:blank",
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        ws_url = None
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1) as r:
                    seiten = json.load(r)
                # Nur echte Tabs: Chrome listet hier auch eigene
                # Extension-Hintergrundseiten, die keine Seite rendern.
                tabs = [
                    s for s in seiten
                    if s.get("type") == "page"
                    and not s.get("url", "").startswith("chrome-extension://")
                ]
                if tabs:
                    ws_url = tabs[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                pass
            await asyncio.sleep(0.25)
        if not ws_url:
            sys.exit("FATAL: Chrome meldet sich nicht am Debug-Port")

        ziel.mkdir(parents=True, exist_ok=True)
        geschrieben = 0
        async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
            dt = Devtools(ws)
            await dt.ruf("Page.enable")
            await dt.ruf("Runtime.enable")

            # Erst die Seite laden, dann den Token setzen (localStorage
            # braucht den Origin), dann neu laden — sonst steht der
            # Login-Schirm im Bild. Der Demo-Stack prueft den Wert nicht.
            await dt.ruf("Page.navigate", url=BASIS)
            await asyncio.sleep(2.5)
            await dt.js("localStorage.setItem('ft8_api_token','demo')")

            for datei, tab, anker, hoehe in ANSICHTEN:
                await dt.ruf(
                    "Emulation.setDeviceMetricsOverride",
                    width=BREITE, height=hoehe, deviceScaleFactor=2, mobile=False,
                )
                await dt.ruf("Page.navigate", url=BASIS)
                await asyncio.sleep(3.0)

                getroffen = await dt.js(
                    "(() => { const b=[...document.querySelectorAll('button')]"
                    f".find(x=>x.textContent.includes({tab!r})); "
                    "if(!b) return false; b.click(); return true; })()"
                )
                if not getroffen:
                    print(f"  ! Tab {tab!r} nicht gefunden — {datei} uebersprungen")
                    continue
                await asyncio.sleep(2.5)

                if anker:
                    await dt.js(
                        "(() => { const e=[...document.querySelectorAll('*')]"
                        ".find(x=>x.children.length===0 && x.textContent.includes("
                        f"{anker!r})); "
                        "if(e) { e.scrollIntoView({block:'start'}); "
                        f"window.scrollBy(0,-{KOPFLEISTE_PX}); }} "
                        "})()"
                    )
                    await asyncio.sleep(1.0)

                bild = await dt.ruf("Page.captureScreenshot", format="png")
                if not bild.get("data"):
                    print(f"  ! kein Bild fuer {datei}")
                    continue
                import base64
                (ziel / datei).write_bytes(base64.b64decode(bild["data"]))
                print(f"  ✓ {datei}  ({BREITE}x{hoehe} @2x)")
                geschrieben += 1
        return geschrieben
    finally:
        proc.terminate()
        shutil.rmtree(profil, ignore_errors=True)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    try:
        with urllib.request.urlopen(f"{BASIS}/api/status", timeout=4):
            pass
    except Exception:
        sys.exit(f"FATAL: unter {BASIS} antwortet nichts — erst ./scripts/dev_run.py starten")

    ziel = Path(sys.argv[1]).resolve()
    n = asyncio.run(hole_bilder(ziel))
    print(f"\n{n} Bilder in {ziel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
