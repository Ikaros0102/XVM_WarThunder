"""Centralized rotating file logging and uncaught exception handlers."""

from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from wt_xvm.paths import get_log_path

_CONFIGURED = False


def setup_logging(log_path: Path | None = None, level: int = logging.INFO) -> logging.Logger:
    """Configure root logger with rotating UTF-8 file handler and console output."""
    global _CONFIGURED
    target_path = log_path or get_log_path()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger("wt_xvm")
    root_logger.setLevel(level)

    if not _CONFIGURED:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        file_handler = RotatingFileHandler(
            target_path,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        root_logger.addHandler(file_handler)

        if sys.stderr is not None:
            console_handler = logging.StreamHandler(sys.stderr)
            console_handler.setFormatter(formatter)
            console_handler.setLevel(level)
            root_logger.addHandler(console_handler)

        def _handle_exception(exc_type, exc_value, exc_traceback) -> None:
            if issubclass(exc_type, KeyboardInterrupt):
                sys.__excepthook__(exc_type, exc_value, exc_traceback)
                return
            root_logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))

        def _handle_thread_exception(args: threading.ExceptHookArgs) -> None:
            root_logger.critical(
                "Uncaught exception in thread %s",
                getattr(args.thread, "name", "unknown"),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )

        sys.excepthook = _handle_exception
        threading.excepthook = _handle_thread_exception
        _CONFIGURED = True

    return root_logger
