from __future__ import annotations

import logging

import uvicorn

from app.api.app import create_app


LOGGER = logging.getLogger(__name__)
app = create_app()


def main() -> int:
    try:
        uvicorn.run(
            "app.main:app",
            host="0.0.0.0",
            port=8000,
            reload=False,
        )
    except Exception as exc:  # pragma: no cover - used for CLI failure reporting
        LOGGER.exception("API startup failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
