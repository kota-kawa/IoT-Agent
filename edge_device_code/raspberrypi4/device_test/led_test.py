#!/usr/bin/env python3
# ~/iot-agent/test/led_test.py
# -*- coding: utf-8 -*-

"""
Raspberry Pi 4 + 3つのLED制御スクリプト（gpiozero版）

これまでのコード（サーボ/TFT）で使用・予約しているGPIOとは
競合しないように、新たに3本のGPIOをLED用に割り当てています。

【これまでのコードで使っている/使わないことにしている GPIO（BCM）】
  サーボ関連:
    - 4, 5, 6, 12, 13, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27
  TFT( ST7735 )関連:
    - 18, 20, 21 (SPI1)
    - 26 (DC), 6 (RES), 13 (BL)
  -> 上記はすべて「LEDでは使わない」前提

【LED用に新規で使用する GPIO（BCM番号 / 物理ピン）】
  LED1 -> GPIO2  / 物理ピン3
  LED2 -> GPIO3  / 物理ピン5
  LED3 -> GPIO16 / 物理ピン36

【配線（各LED共通）】
  - LEDの短い脚（カソード） -> Raspberry Pi の GND
    (物理ピン 6/9/14/20/25/30/34/39 のいずれか)
  - LEDの長い脚（アノード） -> 抵抗(220〜1000Ω程度) -> 下記GPIO
      * LED1: GPIO2  (物理3)
      * LED2: GPIO3  (物理5)
      * LED3: GPIO16 (物理36)

【動作概要】
  - 無引数で起動すると、以下のパターンを繰り返す:
      1) LED1 -> LED2 -> LED3 の順に流れるように点灯
      2) 3つすべて同時に点灯
      3) 3つまとめて高速で点滅
  - Ctrl+C (KeyboardInterrupt) で安全に終了し、LEDを消灯する。
"""

from __future__ import annotations

import time
import sys
import signal
from typing import Optional

from gpiozero import LED


# これまでのコードで使用・予約している GPIO（参考用：ここでは実際には使っていない）
USED_BCM = {
    4, 5, 6, 12, 13, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27
}

# ★ LED用に新しく使うGPIO（これまでのコードと競合していない）
LED1_PIN = 2   # 物理ピン3
LED2_PIN = 3   # 物理ピン5
LED3_PIN = 16  # 物理ピン36

# 念のため、競合チェック（万が一被っていたら起動時に例外）
for pin in (LED1_PIN, LED2_PIN, LED3_PIN):
    if pin in USED_BCM:
        raise RuntimeError(
            f"LED用GPIO GPIO{pin} が USED_BCM と競合しています。"
        )


def pattern_chase(led1: LED, led2: LED, led3: LED, delay: float = 0.2) -> None:
    """
    LED1 -> LED2 -> LED3 の順に「流れる」ように点灯させるパターン。
    """
    # 一旦すべて消灯
    led1.off()
    led2.off()
    led3.off()
    time.sleep(delay)

    # LED1 のみ点灯
    led1.on()
    time.sleep(delay)
    led1.off()

    # LED2 のみ点灯
    led2.on()
    time.sleep(delay)
    led2.off()

    # LED3 のみ点灯
    led3.on()
    time.sleep(delay)
    led3.off()


def pattern_all_on(led1: LED, led2: LED, led3: LED, delay: float = 0.5) -> None:
    """
    3つすべてを同時に点灯させるパターン。
    """
    led1.on()
    led2.on()
    led3.on()
    time.sleep(delay)
    led1.off()
    led2.off()
    led3.off()
    time.sleep(delay)


def pattern_blink_all(led1: LED, led2: LED, led3: LED,
                      delay: float = 0.1, times: int = 5) -> None:
    """
    3つすべてを高速で点滅させるパターン。
    """
    for _ in range(times):
        led1.on()
        led2.on()
        led3.on()
        time.sleep(delay)
        led1.off()
        led2.off()
        led3.off()
        time.sleep(delay)


def main(argv: Optional[list[str]] = None) -> int:
    # Ctrl+C を押したときにすぐ止まるようにしておく（任意）
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    led1 = LED(LED1_PIN)
    led2 = LED(LED2_PIN)
    led3 = LED(LED3_PIN)

    print("=== LEDデモ開始 ===")
    print(f" LED1: GPIO{LED1_PIN} (物理ピン3)")
    print(f" LED2: GPIO{LED2_PIN} (物理ピン5)")
    print(f" LED3: GPIO{LED3_PIN} (物理ピン36)")
    print(" Ctrl+C で終了します。")

    try:
        while True:
            # 1) 流れるパターン
            for _ in range(4):
                pattern_chase(led1, led2, led3, delay=0.15)

            # 2) 全点灯パターン
            for _ in range(3):
                pattern_all_on(led1, led2, led3, delay=0.4)

            # 3) 高速点滅パターン
            pattern_blink_all(led1, led2, led3, delay=0.07, times=8)

    except KeyboardInterrupt:
        print("\n[INFO] キーボード割り込みを受け取りました。終了処理中...")
        return 0
    finally:
        # 終了時は必ずLEDを消灯
        led1.off()
        led2.off()
        led3.off()
        led1.close()
        led2.close()
        led3.close()
        print("[INFO] LEDを消灯し、リソースを解放しました。")


if __name__ == "__main__":
    sys.exit(main())