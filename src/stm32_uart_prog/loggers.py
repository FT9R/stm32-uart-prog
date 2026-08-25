import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


class ContextAdapter(logging.LoggerAdapter):
    """Custom adapter with a dedicated, mutable context attribute"""

    def __init__(self, logger: logging.Logger, context: str | None = None):
        super().__init__(logger, {})
        self._context = context

    def process(self, msg, kwargs):
        """Only prepend context if it is explicitly set"""
        if self._context:
            return f"[ctx {self._context}] {msg}", kwargs
        return msg, kwargs

    def set_context(self, context: str | None):
        """Mutate the context of THIS specific adapter instance"""
        self._context = context

    def with_context(self, context: str):
        """
        Create a NEW adapter around the same underlying logger
        Use this in specific modules to avoid polluting the global context
        """
        return ContextAdapter(self.logger, context)

    def get_level(self):
        """Helper to easily get the current logging level"""
        return logging.getLevelName(self.logger.level)

    def set_level(self, level: str):
        """Helper to easily change the logging level on the fly"""
        if isinstance(level, str):
            level = level.upper()
            self.logger.setLevel(level)


class LevelFilter(logging.Filter):
    """Filter logs to strictly allow one specific level"""

    def __init__(self, level):
        super().__init__()
        self.level = level

    def filter(self, record):
        return record.levelno == self.level


class AutoFlushRotatingFileHandler(RotatingFileHandler):
    """Override RotatingFileHandler to sync the log to disk after each write"""

    def emit(self, record):
        super().emit(record)
        if self.stream and not self.stream.closed:
            self.stream.flush()
            os.fsync(self.stream.fileno())


def setup_logger(
    name: str,
    split: bool = False,
    level: str | None = "DEBUG",
    log_dir: str | Path = "logs",
    max_bytes: int = 10 * 1024 * 1024,
    autoflush: bool = False,
) -> ContextAdapter:
    """
    Factory function to configure and return a logger

    :param name: Name of the logger
    :param split: If `True`, splits logs into separated level files
    :param level: The minimum logging level or `None` to disable logger
    :param log_dir: The directory where logs will be saved
    :param max_bytes: The maximum file size in bytes before rotating
    :param autoflush: If `True`, forces the operating system to flush and sync logs to disk immediately after every write
    """
    logger = logging.getLogger(name)
    if logger.hasHandlers():
        logger.handlers.clear()
    if level is None:
        logger.disabled = True
        return ContextAdapter(logger)
    logger.disabled = False
    level = level.upper()
    if level not in logging._nameToLevel:
        print(f"Invalid level '{level}'. Forcing 'INFO'")
        level = "INFO"
    logger.setLevel(level)
    logger.propagate = False

    # Resolve the path relative to the current working directory if it isn't absolute
    log_path = Path(log_dir)
    if not log_path.is_absolute():
        log_path = Path.cwd() / log_path

    # Check if we're running under systemd and log_dir is "logs"
    # If so, drop the "logs" suffix since systemd provides LogsDirectory
    if log_dir == "logs" and os.environ.get("INVOCATION_ID") and os.environ.get("JOURNAL_STREAM"):
        log_path = Path.cwd()
    log_path.mkdir(parents=True, exist_ok=True)

    HandlerClass = AutoFlushRotatingFileHandler if autoflush else RotatingFileHandler
    if split:
        formatter = logging.Formatter("%(asctime)s.%(msecs)03d - %(filename)s:%(funcName)s:%(lineno)d - %(message)s", datefmt="%d-%m-%Y %H:%M:%S")
        handlers_config = {logging.ERROR: "error.log", logging.WARNING: "warning.log", logging.INFO: "info.log", logging.DEBUG: "debug.log"}
        for lvl, filename in handlers_config.items():
            handler = HandlerClass(filename=log_path / filename, maxBytes=max_bytes, backupCount=2, encoding="utf-8")
            handler.setFormatter(formatter)
            handler.addFilter(LevelFilter(lvl))
            logger.addHandler(handler)
    else:
        formatter = logging.Formatter(
            "%(asctime)s.%(msecs)03d - %(filename)s:%(funcName)s:%(lineno)d(%(levelname)s) - %(message)s",
            datefmt="%d-%m-%Y %H:%M:%S",
        )
        handler = HandlerClass(filename=log_path / f"{name}.log", maxBytes=max_bytes, backupCount=2, encoding="utf-8")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return ContextAdapter(logger)


def _run_log_tests(log_instance: ContextAdapter, label: str):
    """Helper to run all levels on a given logger to keep the demo clean."""
    log_instance.debug(f"DEBUG message from {label}")
    log_instance.info(f"INFO message from {label}")
    log_instance.warning(f"WARNING message from {label}")
    log_instance.error(f"ERROR message from {label}")

    try:
        _ = 1 / 0
    except ZeroDivisionError:
        log_instance.exception(f"EXCEPTION message from {label}")


def _demo():
    loggers = [
        setup_logger("gen", split=True),
        setup_logger("pow"),
    ]

    print("Running WITHOUT context tests...")
    for log in loggers:
        _run_log_tests(log, f"{log.logger.name} (No Context)")

    print("Running WITH context tests...")
    for log in loggers:
        ctx_string = f"{log.logger.name.upper()}_MODULE"
        log_ctx = log.with_context(ctx_string)
        _run_log_tests(log_ctx, f"{log.logger.name}")

    print("Demo complete! Verifying created log file paths:")
    for log in loggers:
        print(f"\nLogger: '{log.logger.name}'")
        paths = [h.baseFilename for h in log.logger.handlers if hasattr(h, "baseFilename")]
        for path in paths:
            print(f"Created file: {path}")


if __name__ == "__main__":
    _demo()
else:
    log = setup_logger("gen", level=None)
