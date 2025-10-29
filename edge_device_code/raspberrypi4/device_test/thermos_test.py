#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DHT11 reader for Raspberry Pi 4 using GPIO26.
- Uses latest Adafruit CircuitPython DHT library + Blinka (libgpiod backend).
- Robust read with retries, optional CSV logging, and graceful cleanup.

Wiring (3-pin breakout assumed, S/+/-):
  S -> GPIO26 (physical pin 40)
  + -> 3V3
  - -> GND
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import adafruit_dht
import board


def resolve_board_pin(pin_number: int):
    """
    Convert BCM pin number (e.g., 26) into board.D26 dynamically.
    """
    try:
        return getattr(board, f"D{pin_number}")
    except AttributeError as exc:
        raise ValueError(
            f"Unsupported GPIO number: {pin_number}. "
            "Use a valid BCM GPIO (e.g., 4, 17, 18, 26, etc.)."
        ) from exc


def create_dht_device(gpio_pin_bcm: int) -> adafruit_dht.DHT11:
    """
    Instantiate DHT11 on the requested BCM pin.
    On Linux SBCs, pulseio is not available; pass use_pulseio=False.
    """
    pin = resolve_board_pin(gpio_pin_bcm)
    # use_pulseio=False is recommended on Raspberry Pi (Blinka + libgpiod).
    return adafruit_dht.DHT11(pin, use_pulseio=False)


def read_once(
    dht: adafruit_dht.DHT11,
    retry: int = 10,
    delay_sec: float = 2.0,
) -> Tuple[float, float]:
    """
    Read temperature (C) and humidity (%) with retry logic.
    DHT11 is slow/noisy; intermittent RuntimeError is normal.
    """
    last_err: Optional[Exception] = None
    for _ in range(retry):
        try:
            temperature_c = dht.temperature  # type: ignore[assignment]
            humidity = dht.humidity  # type: ignore[assignment]
            # Some failures return None; guard against that.
            if temperature_c is not None and humidity is not None:
                return float(temperature_c), float(humidity)
        except RuntimeError as e:
            # Common transient read error; just retry.
            last_err = e
        except Exception as e:  # noqa: BLE001
            # Unexpected error: release sensor and re-raise
            dht.exit()
            raise
        time.sleep(delay_sec)

    # Retries exhausted
    raise RuntimeError(f"DHT11 read failed after {retry} attempts") from last_err


def open_csv_for_append(csv_path: Path):
    """
    Open CSV file for appending and write header if file is new/empty.
    """
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = csv_path.exists() and csv_path.stat().st_size > 0
    f = csv_path.open("a", newline="", encoding="utf-8")
    writer = csv.writer(f)
    if not file_exists:
        writer.writerow(["timestamp", "temperature_c", "humidity_percent"])
    return f, writer


def run(args: argparse.Namespace) -> int:
    dht = create_dht_device(args.pin)

    csv_file = None
    csv_writer = None
    if args.csv:
        csv_file, csv_writer = open_csv_for_append(Path(args.csv))

    try:
        if args.once:
            t_c, h = read_once(dht, retry=args.retry, delay_sec=args.delay)
            now = datetime.now().isoformat(timespec="seconds")
            print(f"[{now}] T={t_c:.1f}°C  H={h:.1f}%")
            if csv_writer:
                csv_writer.writerow([now, f"{t_c:.1f}", f"{h:.1f}"])
            return 0

        # Continuous mode
        print("Starting DHT11 read loop. Press Ctrl+C to stop.")
        while True:
            try:
                t_c, h = read_once(dht, retry=args.retry, delay_sec=args.delay)
                now = datetime.now().isoformat(timespec="seconds")
                print(f"[{now}] T={t_c:.1f}°C  H={h:.1f}%")
                if csv_writer:
                    csv_writer.writerow([now, f"{t_c:.1f}", f"{h:.1f}"])
                    # Ensure data is flushed to disk promptly.
                    csv_file.flush()
            except RuntimeError as e:
                # Log and continue; sensor is often flaky
                now = datetime.now().isoformat(timespec="seconds")
                print(f"[{now}] Read error: {e}", file=sys.stderr)
            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\nInterrupted by user. Exiting...")
        return 0
    finally:
        # Always release the sensor resources.
        try:
            dht.exit()
        except Exception:
            pass
        if csv_file:
            csv_file.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Read DHT11 (GPIO26 by default) on Raspberry Pi using Adafruit CircuitPython.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--pin",
        type=int,
        default=26,  # ← こうたさん指定のGPIO26
        help="BCM GPIO number connected to DHT11 data pin (e.g., 26).",
    )
    p.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Interval between successive readings in continuous mode (seconds).",
    )
    p.add_argument(
        "--retry",
        type=int,
        default=10,
        help="Per-sample retry count to tolerate transient read failures.",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=0.8,
        help="Delay between retries when a read fails (seconds).",
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="Read only once and exit.",
    )
    p.add_argument(
        "--csv",
        type=str,
        default="",
        help="Optional CSV path to append readings (columns: timestamp, temperature_c, humidity_percent).",
    )
    return p


if __name__ == "__main__":
    sys.exit(run(build_parser().parse_args()))
