import logging
import os
from datetime import datetime


def setup_logger(name: str, log_file: str = "logs/trading.log", level: str = "INFO") -> logging.Logger:
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not logger.handlers:
        fmt = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        fh = logging.FileHandler(log_file)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        logger.addHandler(ch)

    return logger


def get_trade_logger() -> logging.Logger:
    today = datetime.now().strftime("%Y%m%d")
    return setup_logger("trade", f"logs/trades_{today}.log")
