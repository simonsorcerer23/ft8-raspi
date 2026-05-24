"""FastAPI app factory.

Kept in a factory so tests can spin up isolated instances without
touching the module-level singleton.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from ..config import load_config
from ..db import create_all, init_engine
from ..runtime import Orchestrator, build_production_orchestrator
from .routes import (
    adif,
    captive,
    config,
    control,
    healthcheck,
    integrations,
    network,
    sse,
    stats,
    status,
    system,
)
from .routes import log as log_routes

_STATIC_DIR = Path(__file__).parent / "static"
_DEFAULT_CONFIG_PATH = Path("/etc/ft8-appliance/config.yaml")
_DEFAULT_DB_PATH = Path("/var/lib/ft8-appliance/qso.sqlite")

logger = logging.getLogger(__name__)

# Uvicorn hängt seine eigene Logging-Config rein und unsere log.info-
# Aufrufe (QRZ-Upload-Erfolge, PSK-Reporter-Flushes, Mode-Wechsel)
# liefen dadurch ins Leere — am Morgen war's blindes Debuggen.
# Wir hängen einen StreamHandler explizit an den ft8_appliance-
# Namespace damit die INFO-Lines in stderr (= journal) landen.
_ft8_log = logging.getLogger("ft8_appliance")
_ft8_log.setLevel(logging.INFO)
if not _ft8_log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    _ft8_log.addHandler(_h)
    _ft8_log.propagate = False  # sonst doppelt via root


@asynccontextmanager
async def _production_lifespan(app: FastAPI):
    """FastAPI lifespan that owns the production orchestrator.

    Only kicks in when ``app.state.orchestrator`` hasn't been attached
    yet (tests inject their own and bypass this). Loads the config from
    ``$FT8_CONFIG`` (or ``/etc/ft8-appliance/config.yaml``), builds the
    production orchestrator, starts it, and tears it down on shutdown.

    Each step degrades softly: a missing config or hardware adapter
    failure logs a warning but lets the web layer come up — the routes
    that need the orchestrator return 503 in the meantime, so the user
    can still hit ``/`` and fix the config via the wizard.
    """
    pre_attached = getattr(app.state, "orchestrator", None)
    if pre_attached is None:
        config_path = Path(os.environ.get("FT8_CONFIG", _DEFAULT_CONFIG_PATH))
        db_path = Path(os.environ.get("FT8_DB", _DEFAULT_DB_PATH))
        try:
            cfg = load_config(config_path)
            logger.info("loaded config from %s (callsign=%s, rig=%s, mode=%s)",
                        config_path, cfg.operator.callsign, cfg.rig.model,
                        cfg.operating.mode)
            # Initialise the global SQLAlchemy engine + create tables.
            # create_all() is idempotent so re-running on every boot is fine.
            db_path.parent.mkdir(parents=True, exist_ok=True)
            init_engine(db_path)
            # Pass aktiven Callsign zum Backfill der Multi-Operator-Migration:
            # alle bestehenden QSO/Blacklist/Heard-Rows ohne user_callsign
            # bekommen den damaligen Single-Operator zugewiesen.
            await create_all(default_user_callsign=cfg.operator.callsign)
            logger.info("db initialised at %s", db_path)
            orch = await build_production_orchestrator(cfg)
            try:
                await orch.start()
                app.state.orchestrator = orch
                logger.info("orchestrator started")
            except Exception as exc:
                logger.exception("orchestrator.start() failed — running without: %s", exc)
        except FileNotFoundError:
            logger.warning("config %s not found — running without orchestrator "
                           "(first-boot wizard should populate it)", config_path)
        except Exception as exc:
            logger.exception("config load failed — running without orchestrator: %s", exc)

    try:
        yield
    finally:
        # Only stop the orchestrator we started ourselves; leave caller-
        # injected ones alone (tests manage their own lifecycle).
        if pre_attached is None:
            orch = getattr(app.state, "orchestrator", None)
            if orch is not None:
                try:
                    await orch.stop()
                except Exception as exc:
                    logger.warning("orchestrator.stop() raised: %s", exc)


def create_app(orchestrator: Orchestrator | None = None) -> FastAPI:
    """Build a FastAPI app, optionally pre-attached to an orchestrator.

    Production wiring attaches the long-lived orchestrator. Tests can
    either pass in a fake or attach one later via ``app.state``.
    """
    app = FastAPI(
        title="FT8 Hochgericht Appliance",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=_production_lifespan,
    )

    if orchestrator is not None:
        app.state.orchestrator = orchestrator

    # Captive-portal probe URLs — registered *before* SPA fallback so they win
    captive.register(app)

    # Real API
    app.include_router(status.router, prefix="/api", tags=["status"])
    app.include_router(healthcheck.router, prefix="/api", tags=["healthcheck"])
    app.include_router(control.router, prefix="/api/control", tags=["control"])
    app.include_router(log_routes.router, prefix="/api", tags=["log"])
    app.include_router(config.router, prefix="/api", tags=["config"])
    app.include_router(integrations.router, prefix="/api", tags=["integrations"])
    app.include_router(network.router, prefix="/api", tags=["network"])
    app.include_router(stats.router, prefix="/api", tags=["stats"])
    app.include_router(system.router, prefix="/api", tags=["system"])
    app.include_router(adif.router, prefix="/api", tags=["adif"])
    from .routes import operators as operators_routes
    app.include_router(operators_routes.router, prefix="/api", tags=["operators"])
    app.include_router(sse.router, tags=["sse"])

    # Mount the SPA build output under /assets/ + serve index.html at /.
    # The Vite build target is exactly _STATIC_DIR, so this is wired
    # automatically once `npm run build` has run.
    assets = _STATIC_DIR / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    # Offline map tiles — populated by scripts/fetch_offline_tiles.sh on the
    # Pi. Frontend Leaflet hits /tiles/{z}/{x}/{y}.png. If the directory
    # isn't there (dev workstation), the frontend falls back to online OSM.
    #
    # Tiles liegen unter /var/lib (NICHT im git-Workdir) damit Self-Update
    # / git checkout sie nie anfasst. Selber Persistenz-Mountpoint wie die
    # qso.sqlite-DB.
    tiles_dir = Path("/var/lib/ft8-appliance/tiles")
    if tiles_dir.is_dir():
        app.mount("/tiles", StaticFiles(directory=str(tiles_dir)), name="tiles")

    @app.get("/", include_in_schema=False)
    async def root() -> Response:
        index = _STATIC_DIR / "index.html"
        if index.is_file():
            # No-Cache fuer index.html — die referenzierten Asset-Pfade
            # haben Inhalts-Hashes (z.B. index-DlTL-Afk.js) und koennen
            # daher beliebig lange gecacht werden, aber die HTML muss
            # bei jedem Reload neu vom Server kommen, sonst sieht der
            # Browser den alten Hash und laed weiter den alten Bundle.
            # Sebastian sah 2026-05-23: nach Frontend-Refactor zeigte
            # die UI "genauso aus wie vorher", weil die HTML gecached
            # war und auf den vorherigen Bundle zeigte.
            return FileResponse(
                str(index),
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
        return Response(
            content="FT8 Hochgericht — controller is running.\n"
                    "Frontend not built. Run `cd frontend && npm run build`.",
            media_type="text/plain",
        )

    @app.get("/favicon.svg", include_in_schema=False)
    async def favicon_svg() -> Response:
        svg = _STATIC_DIR / "favicon.svg"
        if svg.is_file():
            return FileResponse(str(svg), media_type="image/svg+xml")
        return Response(status_code=204)

    @app.get("/manifest.webmanifest", include_in_schema=False)
    async def manifest() -> Response:
        m = _STATIC_DIR / "manifest.webmanifest"
        if m.is_file():
            return FileResponse(str(m), media_type="application/manifest+json")
        return Response(status_code=204)

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        return Response(status_code=204)

    return app


# Module-level instance used by uvicorn in production. Orchestrator is
# attached at boot in main.py.
app = create_app()
