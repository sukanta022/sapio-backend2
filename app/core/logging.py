
import logging
import sys


LOG_FORMAT = (
    "%(asctime)s | "
    "%(levelname)s | "
    "%(name)s | "
    "%(message)s"
)

HANDLER_NAME = "sapio_console_handler"


def setup_logging(log_level: str = "INFO") -> None:
    logger = logging.getLogger("sapio")

    logger.setLevel(log_level)
    logger.propagate = False

    for handler in logger.handlers:
        if handler.get_name() == HANDLER_NAME:
            return

    handler = logging.StreamHandler(sys.stdout)
    handler.set_name(HANDLER_NAME)

    handler.setFormatter(
        logging.Formatter(LOG_FORMAT)
    )

    logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"sapio.{name}")
