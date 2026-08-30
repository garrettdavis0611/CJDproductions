from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup_logging(level: str = "INFO", log_file: str | Path | None = None) -> None:
    handlers: list[logging.Handler] = []

    try:
        from rich.logging import RichHandler

        handlers.append(RichHandler(rich_tracebacks=True, show_path=False, markup=False))
        fmt = "%(message)s"
    except ImportError:
        handler = logging.StreamHandler(sys.stderr)
        handlers.append(handler)
        fmt = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

    if log_file is not None:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        handlers.append(file_handler)

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        datefmt="%H:%M:%S",
        handlers=handlers,
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
