import logging
import os


def setup_logging():
    level = logging.DEBUG if os.getenv("DEBUG", "").strip().lower() in ("1", "true", "yes", "on") else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )