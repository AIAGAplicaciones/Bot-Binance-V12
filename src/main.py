"""Entry point. Carga .env, configura logging y arranca uvicorn con la app."""
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)


def main() -> None:
    import uvicorn
    port = int(os.getenv("PORT", "8000"))  # Railway inyecta PORT
    uvicorn.run("src.live.server:app", host="0.0.0.0", port=port, log_level=LOG_LEVEL.lower())


if __name__ == "__main__":
    main()
