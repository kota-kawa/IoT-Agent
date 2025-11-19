#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jetson Orin Nano の J12(40pin) を起動直後から GPIO として使える状態に自動調整するツール。

ポイント:
- PIR 用:   37番ピン（BOARD, BCM26）を GPIO "INPUT"（PUD_DOWN）に自動補正
- HC-SR04:  31番ピン（BOARD, BCM6） も GPIO "INPUT"（PUD_DOWN）に自動補正
- それ以外の信号ピンは従来通り "OUTPUT" として GPIO 化
- Jetson.GPIO の Warning に出る 'busybox devmem <addr> w <val>' を自動抽出・適用して pinmux/方向を強制反映
"""

import re
import subprocess
import sys
import time
import warnings
import shutil

import Jetson.GPIO as GPIO

# ===== 設定 =====
# I2C/PWM は GPIO 化から除外（BOARD番号）
RESERVED = {3, 5, 27, 28, 32, 33}

# 電源(1,2,4,17) / GND(6,9,14,20,25,30,34,39) を除いた「信号ピン」（BOARD番号）
ALL_SIGNAL_PINS = [
    # 奇数側
    3, 5, 7, 11, 13, 15, 19, 21, 23, 27, 29, 31, 33, 35, 37,
    # 偶数側
    8, 10, 12, 16, 18, 22, 24, 26, 28, 32, 36, 38, 40,
]
USABLE_PINS = [p for p in ALL_SIGNAL_PINS if p not in RESERVED]

# 起動時に必ず INPUT にしたいピン（BOARD番号）
# - 37: PIR SR501 の OUT（BCM26）
# - 31: HC-SR04 の ECHO（BCM6）
INPUT_PINS = {37, 31}

# busybox の devmem を使う（パス自動検出）
DEV_MEM_BIN = shutil.which("busybox") or "/bin/busybox"

# ===== 実装 =====
def _apply_devmem_from_warning_text(text: str) -> bool:
    """
    Jetson.GPIO の Warning 文から 'devmem <addr> w <val>' を抽出して実行。
    成功したら True、抽出不可または失敗なら False。
    """
    m = re.search(r"devmem\s+(0x[0-9A-Fa-f]+)\s+w\s+(0x[0-9A-Fa-f]+)", text)
    if not m:
        return False
    addr, val = m.group(1), m.group(2)
    try:
        subprocess.run([DEV_MEM_BIN, "devmem", addr, "w", val], check=True)
        print(f"[APPLY] devmem {addr} w {val}", flush=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] devmem failed: {e}", flush=True)
        return False

def _setup_dir_with_autofix(board_pin: int, direction: int, pud=None) -> bool:
    """
    指定 BOARD ピンを direction(IN/OUT) で setup。必要なら devmem を適用して再試行。
    戻り値: True=最終的に設定できた / False=できなかった
    """
    ok = False
    with warnings.catch_warnings(record=True) as ws:
        warnings.simplefilter("always")
        try:
            if direction == GPIO.OUT:
                GPIO.setup(board_pin, GPIO.OUT, initial=GPIO.LOW)
            else:
                if pud is not None:
                    GPIO.setup(board_pin, GPIO.IN, pull_up_down=pud)
                else:
                    GPIO.setup(board_pin, GPIO.IN)
            ok = True
        except Exception as e:
            print(f"[INFO] setup({board_pin}) raised: {e}", flush=True)

        # 捕まえた Warning の中に devmem 提示があれば適用して再試行
        for w in ws:
            msg = str(w.message)
            if "devmem" in msg and _apply_devmem_from_warning_text(msg):
                try:
                    if direction == GPIO.OUT:
                        GPIO.setup(board_pin, GPIO.OUT, initial=GPIO.LOW)
                    else:
                        if pud is not None:
                            GPIO.setup(board_pin, GPIO.IN, pull_up_down=pud)
                        else:
                            GPIO.setup(board_pin, GPIO.IN)
                    ok = True
                except Exception as e2:
                    print(f"[WARN] retry setup({board_pin}) failed: {e2}", flush=True)
    return ok

def main():
    print("[INFO] j12_gpio_bootstrap: start", flush=True)
    print(f"[INFO] RESERVED (skip): {sorted(RESERVED)}", flush=True)
    print(f"[INFO] TARGET (GPIO化対象): {sorted(USABLE_PINS)}", flush=True)
    print(f"[INFO] INPUT_PINS: {sorted(INPUT_PINS)}", flush=True)

    GPIO.setmode(GPIO.BOARD)
    GPIO.setwarnings(True)

    ok_pins, ng_pins = [], []

    try:
        for pin in USABLE_PINS:
            is_input = pin in INPUT_PINS
            label = "IN(PUD_DOWN)" if is_input else "OUT"
            print(f"[PIN] BOARD {pin}: configure as GPIO {label} ...", flush=True)

            if is_input:
                ok = _setup_dir_with_autofix(pin, GPIO.IN, pud=GPIO.PUD_DOWN)
            else:
                ok = _setup_dir_with_autofix(pin, GPIO.OUT)

            if ok:
                ok_pins.append(pin)
            else:
                ng_pins.append(pin)

            # pinmux/方向設定だけ行い、レベル保持はしない（解放）
            try:
                GPIO.cleanup(pin)
            except Exception:
                pass

            time.sleep(0.02)  # 軽いインターバル

        # サマリ
        print("\n=== J12 GPIO bootstrap summary ===", flush=True)
        print(f"OK  ({len(ok_pins)}): {sorted(ok_pins)}", flush=True)
        if ng_pins:
            print(f"NG  ({len(ng_pins)}): {sorted(ng_pins)}", flush=True)
            print("Note: NG はドライバ占有や特殊用途の可能性。個別に確認してください。", flush=True)
    finally:
        GPIO.cleanup()
        print("[INFO] j12_gpio_bootstrap: done", flush=True)

if __name__ == "__main__":
    try:
        main()
        sys.exit(0)
    except KeyboardInterrupt:
        print("\nInterrupted.", flush=True)
        sys.exit(130)
