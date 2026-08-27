import logging
import os
from datetime import datetime


def configure_logging(log_dir="logs"):
    """Configure package logging once for a CLI sync process."""
    package_logger = logging.getLogger("weread2notionpro")
    if package_logger.handlers:
        return None

    os.makedirs(log_dir, exist_ok=True)
    log_filename = os.path.join(
        log_dir,
        f"weread_sync_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
    )
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    package_logger.addHandler(console_handler)

    file_handler = logging.FileHandler(log_filename, encoding="utf-8")
    file_handler.setFormatter(formatter)
    package_logger.addHandler(file_handler)

    package_logger.setLevel(logging.INFO)
    package_logger.propagate = False
    package_logger.info(f"日志文件已创建: {log_filename}")
    return log_filename
