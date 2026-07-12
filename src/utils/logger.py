# -*- coding: utf-8 -*-
"""Logging configuration"""
import logging
from pathlib import Path

def setup_logging(level=logging.INFO):
    log_dir = Path(__file__).parent.parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_dir / "stock_intelligence.log", encoding="utf-8"),
        ]
    )

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
