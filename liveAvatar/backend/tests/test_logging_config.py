import logging

from app.logging_config import configure_logging


def test_configure_logging_sets_info_level():
    root = logging.getLogger()
    previous_level = root.level
    previous_handlers = list(root.handlers)
    try:
        root.handlers = []
        configure_logging()
        assert root.level == logging.INFO
        assert len(root.handlers) >= 1
    finally:
        root.level = previous_level
        root.handlers = previous_handlers


def test_configure_logging_is_idempotent():
    configure_logging()
    configure_logging()
    root = logging.getLogger()
    assert root.level == logging.INFO
