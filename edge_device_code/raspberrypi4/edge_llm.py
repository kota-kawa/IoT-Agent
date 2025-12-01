#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM loader utilities."""

import logging
import sys
from pathlib import Path

from llama_cpp import Llama

import edge_config as config


def _create_llm() -> Llama:
    if not Path(config.MODEL_PATH).exists():
        logging.error("Model file not found: %s", config.MODEL_PATH)
        sys.exit(1)

    logging.info("Loading model from %s", config.MODEL_PATH)
    return Llama(
        model_path=config.MODEL_PATH,
        n_threads=config.LLAMA_THREADS,
        n_ctx=config.LLAMA_CONTEXT,
        verbose=False,
    )


__all__ = ["_create_llm"]
