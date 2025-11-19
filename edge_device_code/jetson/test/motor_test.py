#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
L293D を PWM なし（ENピン常時HIGH）で駆動するサンプル。
- EN(1,2EN / 3,4EN) は L293D の Vcc1(5V) へ直結（※Jetsonには配線しない）。
- Jetson からは 1A/2A/3A/4A の 4 本のみを出力。
- 動作中は常にフルスピード（速度制御なし）。方向のみ制御。

配線（BCM -> L293D）:
  BCM24 -> 1A (pin 2)
  BCM23 -> 2A (pin 7)
  BCM27 -> 3A (pin 10)
  BCM22 -> 4A (pin 15)
  Jetson GND <-> L293D GND (pins 4,5,12,13)
  L293D Vcc1 (pin 16) = 5V,  1,2EN(pin1)=HIGH, 3,4EN(pin9)=HIGH（Vcc1直結）
  L293D Vs (pin 8) = モータ電源（例: 6–12V）
"""

import time
import Jetson.GPIO as GPIO

# ==== 既存の方向ピンはそのまま使う（BCM） ====
IN1 = 24  # -> 1A (Motor A)
IN2 = 23  # -> 2A (Motor A)
IN3 = 27  # -> 3A (Motor B)
IN4 = 22  # -> 4A (Motor B)

def setup():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    for pin in (IN1, IN2, IN3, IN4):
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)

def forward():
    # Motor A: 1A=H, 2A=L   /   Motor B: 3A=H, 4A=L
    GPIO.output(IN1, GPIO.HIGH)
    GPIO.output(IN2, GPIO.LOW)
    GPIO.output(IN3, GPIO.HIGH)
    GPIO.output(IN4, GPIO.LOW)

def reverse():
    # Motor A: 1A=L, 2A=H   /   Motor B: 3A=L, 4A=H
    GPIO.output(IN1, GPIO.LOW)
    GPIO.output(IN2, GPIO.HIGH)
    GPIO.output(IN3, GPIO.LOW)
    GPIO.output(IN4, GPIO.HIGH)

def brake():
    # 両入力HIGHで出力短絡ブレーキ（L293Dの仕様）
    GPIO.output(IN1, GPIO.HIGH)
    GPIO.output(IN2, GPIO.HIGH)
    GPIO.output(IN3, GPIO.HIGH)
    GPIO.output(IN4, GPIO.HIGH)

def coast():
    # 両入力LOWで惰性停止
    GPIO.output(IN1, GPIO.LOW)
    GPIO.output(IN2, GPIO.LOW)
    GPIO.output(IN3, GPIO.LOW)
    GPIO.output(IN4, GPIO.LOW)

if __name__ == "__main__":
    try:
        setup()
        print("FULL power (no PWM). Forward 3s -> Brake 1s -> Reverse 3s -> Coast.")
        forward()
        time.sleep(3.0)
        brake()
        time.sleep(1.0)
        reverse()
        time.sleep(3.0)
        coast()
        print("Done. Press Ctrl+C to exit or modify the loop as needed.")
        # 以降は必要なら while True: ... に切替
    except KeyboardInterrupt:
        pass
    finally:
        coast()
        GPIO.cleanup()
