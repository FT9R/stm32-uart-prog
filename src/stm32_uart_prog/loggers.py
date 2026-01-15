import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from stm32_uart_prog.colors import *


class LevelFilter(logging.Filter):
    """Filter logs to only allow one specific level"""

    def __init__(self, level):
        super().__init__()
        self.level = level

    def filter(self, record):
        return record.levelno == self.level


class AutoFlushRotatingFileHandler(RotatingFileHandler):
    """Override RotatingFileHandler to flush the log after each write"""

    def emit(self, record):
        super().emit(record)
        self.stream.flush()
        os.fsync(self.stream.fileno())  # Ensure the log is written to disk


class Loggers:
    __entry_dir = os.getcwd()
    levels = logging._levelToName.values()

    def __init__(self):
        raise NotImplementedError(
            f"{self.__class__} is abstract and cannot be instantiated. Use class methods directly."
        )

    @classmethod
    def general_log_setup(cls):
        log_dir = os.path.join(cls.__entry_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)
        ERROR_LOG_PATH = os.path.join(log_dir, "error.log")
        WARNING_LOG_PATH = os.path.join(log_dir, "warning.log")
        INFO_LOG_PATH = os.path.join(log_dir, "info.log")
        DEBUG_LOG_PATH = os.path.join(log_dir, "debug.log")

        formatter = logging.Formatter(
            "%(asctime)s.%(msecs)03d - %(filename)s:%(funcName)s:%(lineno)d - %(message)s", datefmt="%d-%m-%Y %H:%M:%S"
        )

        logger = logging.getLogger("general_logger")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        logger.handlers.clear()

        error_handler = RotatingFileHandler(
            filename=ERROR_LOG_PATH, maxBytes=10 * 1024 * 1024, backupCount=2, encoding="utf-8"
        )
        error_handler.setFormatter(formatter)
        error_handler.addFilter(LevelFilter(logging.ERROR))

        warning_handler = RotatingFileHandler(
            filename=WARNING_LOG_PATH, maxBytes=10 * 1024 * 1024, backupCount=2, encoding="utf-8"
        )
        warning_handler.setFormatter(formatter)
        warning_handler.addFilter(LevelFilter(logging.WARNING))

        info_handler = RotatingFileHandler(
            filename=INFO_LOG_PATH, maxBytes=10 * 1024 * 1024, backupCount=2, encoding="utf-8"
        )
        info_handler.setFormatter(formatter)
        info_handler.addFilter(LevelFilter(logging.INFO))

        debug_handler = RotatingFileHandler(
            filename=DEBUG_LOG_PATH, maxBytes=10 * 1024 * 1024, backupCount=2, encoding="utf-8"
        )
        debug_handler.setFormatter(formatter)
        debug_handler.addFilter(LevelFilter(logging.DEBUG))

        logger.addHandler(error_handler)
        logger.addHandler(warning_handler)
        logger.addHandler(info_handler)
        logger.addHandler(debug_handler)
        return logger

    @classmethod
    def power_log_setup(cls):
        log_dir = os.path.join(cls.__entry_dir, "logs")
        POWER_LOG_PATH = os.path.join(log_dir, "pow.log")

        formatter = logging.Formatter(
            "%(asctime)s.%(msecs)03d - %(filename)s:%(funcName)s:%(lineno)d(%(levelname)s) - %(message)s",
            datefmt="%d-%m-%Y %H:%M:%S",
        )

        logger = logging.getLogger("power_logger")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        logger.handlers.clear()
        handler = AutoFlushRotatingFileHandler(
            filename=POWER_LOG_PATH, maxBytes=10 * 1024 * 1024, backupCount=2, encoding="utf-8"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        return logger

    @classmethod
    def set_level(cls, logger: logging.Logger, level: str):
        if level is None:
            loggerLevel = "INFO"
            print(f"{BOLD}{YELLOW}Logging level has not been provided. Forcing '{loggerLevel}' level{RESET}")
        else:
            loggerLevel = level.upper()

        if loggerLevel not in cls.levels:
            raise ValueError(f"wrong level `{loggerLevel}` for current logger")

        logger.setLevel(loggerLevel)
        print(f"Logging level is set to: {loggerLevel}")

    @staticmethod
    def demo():
        loggers = [Loggers.general_log_setup(), Loggers.power_log_setup()]

        for logger in loggers:
            logger.debug(f"DEBUG from {logger.name}")
            logger.info(f"INFO from {logger.name}")
            logger.warning(f"WARNING from {logger.name}")
            logger.error(f"ERROR from {logger.name}")

            try:
                inf = 1 / 0

            except ZeroDivisionError as zde:
                logger.exception(f"EXCEPTION from {logger.name}: {zde}")


entry = sys.modules.get("__main__")

if entry and hasattr(entry, "__file__") and entry.__file__:
    caller = os.path.basename(entry.__file__)
    print(f"Logging system is used by: {caller}")
else:
    print(f"{BOLD}{RED}Could not check for app entry{RESET}")
    sys.exit()

if caller in ("__main__.py", "main.py", "stm32-uart-prog"):
    logger = Loggers.general_log_setup()
    # logger_pow = Loggers.power_log_setup()

elif caller == os.path.basename(__file__):
    Loggers.demo()
else:
    print(f"{BOLD}{RED}Unexpected entry: {entry}{RESET}")
    sys.exit()
