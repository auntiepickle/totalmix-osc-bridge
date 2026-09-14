"""Process-wide logging: console + rotating bridge.log in the data dir.

`configure_logging()` is called by every entry point (`python -m tmosc`, the
frozen exe, `create_app()`, the headless `python -m tmosc.bridge`) and is
idempotent - the second and later calls are no-ops, so a test that builds
several apps never stacks handlers. It used to be a `logging.basicConfig`
at import time in tmosc/bridge.py (#27 phase 4b moved it here).
"""
import logging
import logging.handlers

from tmosc.config import BRIDGE_LOG_FILE, LOG_MAX_BYTES, LOG_BACKUP_COUNT

_MARK = "tmosc_configured"


def configure_logging(level=logging.INFO):
    """Install the console + rotating-file handlers on the root logger once.
    Creates bridge.log immediately (the frozen smoke test checks for it at
    boot). Returns True when this call did the setup, False if already done."""
    root = logging.getLogger()
    if getattr(root, _MARK, False):
        return False
    fmt = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s',
                            datefmt='%H:%M:%S')
    handlers = [logging.StreamHandler(),
                logging.handlers.RotatingFileHandler(
                    BRIDGE_LOG_FILE, maxBytes=LOG_MAX_BYTES,
                    backupCount=LOG_BACKUP_COUNT, encoding='utf-8')]
    for h in handlers:
        h.setFormatter(fmt)
        root.addHandler(h)
    root.setLevel(level)
    setattr(root, _MARK, True)
    return True
