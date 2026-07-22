"""Process-wide logging setup, called once from `app.main` at import time."""

import logging


def configure_logging() -> None:
    """Configure the root logger for the whole app: INFO level and a single
    timestamped `time LEVEL name: message` format on stderr. Called once at
    startup before anything logs, so every module's `logging.getLogger(...)`
    inherits this configuration."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
