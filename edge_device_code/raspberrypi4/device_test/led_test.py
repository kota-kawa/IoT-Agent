#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LED control script for Raspberry Pi using gpiozero.

Wiring (as per ushiken.net article):
  LED anode (+) -> 470Ω resistor -> GPIO17 (BCM)
  LED cathode (-) -> GND

Usage examples:
  python3 led17_gpiozero.py on
  python3 led17_gpiozero.py off
  python3 led17_gpiozero.py blink -n 5 -i 1.0
  python3 led17_gpiozero.py menu
  python3 led17_gpiozero.py --pin 22 blink -n 10 -i 0.2  # if you wired another pin
"""

import argparse
import sys
import signal
from time import sleep
from gpiozero import LED


def blink(led: LED, times: int = 5, interval: float = 1.0) -> None:
    """
    Blink the LED 'times' times at 'interval' seconds on/off.
    Ensures LED is off at the end.
    """
    try:
        for _ in range(max(0, times)):
            led.on()
            sleep(max(0.0, interval))
            led.off()
            sleep(max(0.0, interval))
    finally:
        led.off()


def interactive_menu(led: LED) -> None:
    """
    Simple interactive loop using stdin.
    Commands:
      o: on   f: off   b: blink (ask times/interval)   q: quit
    """
    print("\n--- LED Menu ---")
    print("  o = ON (stays on)")
    print("  f = OFF")
    print("  b = BLINK (you will be asked times/interval)")
    print("  q = QUIT")
    while True:
        try:
            cmd = input("\nEnter command [o/f/b/q]: ").strip().lower()
        except EOFError:
            print("\nEOF received; exiting.")
            break

        if cmd == "o":
            led.on()
            print("LED: ON")
        elif cmd == "f":
            led.off()
            print("LED: OFF")
        elif cmd == "b":
            try:
                times = int(input("  times (e.g., 5): ").strip())
            except Exception:
                print("  Invalid 'times'; using 5.")
                times = 5
            try:
                interval = float(input("  interval seconds (e.g., 1.0): ").strip())
            except Exception:
                print("  Invalid 'interval'; using 1.0.")
                interval = 1.0
            print(f"Blinking {times} times at {interval}s interval…")
            blink(led, times=times, interval=interval)
            print("Done blinking.")
        elif cmd == "q":
            break
        else:
            print("Unknown command.")


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Control an LED via gpiozero (default BCM pin 17)."
    )
    parser.add_argument(
        "--pin",
        type=int,
        default=21,
        help="BCM GPIO pin number the LED anode is connected to via a resistor (default: 17).",
    )

    subparsers = parser.add_subparsers(dest="cmd")

    # on
    sp_on = subparsers.add_parser("on", help="Turn the LED on and wait for Enter to exit.")
    # off
    sp_off = subparsers.add_parser("off", help="Turn the LED off and exit.")
    # blink
    sp_blink = subparsers.add_parser("blink", help="Blink the LED.")
    sp_blink.add_argument(
        "-n", "--times", type=int, default=5, help="Number of blinks (default: 5)"
    )
    sp_blink.add_argument(
        "-i", "--interval", type=float, default=1.0, help="Seconds for on/off interval (default: 1.0)"
    )
    # menu
    subparsers.add_parser("menu", help="Interactive text menu.")

    return parser.parse_args(argv)


def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    args = parse_args(argv)

    # Create LED object; active_high=True because anode -> GPIO, cathode -> GND
    led = LED(args.pin, active_high=True)

    # Ensure clean exit on Ctrl+C
    def _cleanup(sig, frame):
        try:
            led.off()
        finally:
            sys.exit(0)

    signal.signal(signal.SIGINT, _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)

    try:
        if args.cmd == "on":
            led.on()
            print("LED is ON. Press Enter to turn it off and exit.")
            try:
                input()
            except EOFError:
                pass
            return 0
        elif args.cmd == "off":
            led.off()
            print("LED is OFF.")
            return 0
        elif args.cmd == "blink":
            blink(led, times=args.times, interval=args.interval)
            return 0
        else:
            # default to interactive menu
            interactive_menu(led)
            return 0
    finally:
        # Always turn off on exit
        led.off()


if __name__ == "__main__":
    raise SystemExit(main())
