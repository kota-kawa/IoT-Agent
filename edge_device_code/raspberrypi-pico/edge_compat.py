import sys
import time
import random
import gc

# MicroPython/CPython 互換インポートを1か所にまとめる
try:
    import network  # type: ignore
except Exception:
    network = None

try:
    import ure as re  # type: ignore
except Exception:
    import re

try:
    import ujson as json  # type: ignore
except Exception:
    import json

try:
    import usocket as socket  # type: ignore
except Exception:
    import socket

try:
    import ussl as ssl  # type: ignore
except Exception:
    import ssl

try:
    import uio as io  # type: ignore
except Exception:
    import io

try:
    import builtins  # print のラップに使用
except Exception:
    builtins = None  # ありえないが念のため

from machine import Pin, ADC, PWM, unique_id  # type: ignore

__all__ = [
    "sys",
    "time",
    "random",
    "gc",
    "network",
    "re",
    "json",
    "socket",
    "ssl",
    "io",
    "builtins",
    "Pin",
    "ADC",
    "PWM",
    "unique_id",
]
