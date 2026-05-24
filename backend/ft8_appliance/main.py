"""Application entrypoint.

Wired up incrementally as the phases land. For now a thin placeholder
so the package can be imported / installed in editable mode.
"""

from __future__ import annotations

import logging


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log = logging.getLogger("ft8")
    log.info("ft8-appliance starting (skeleton stage)")
    log.info("real wiring lands in phase D")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
