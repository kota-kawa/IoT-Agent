#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ST7735 1.8inch 128x160 TFT（基板シルク: GND VCC SCL SDA RES DC CS BL）
Raspberry Pi 4 / SPI1 で動作確認用のシンプルなデモ。

配線（BCM番号 / 物理ピン）:
  SCL -> GPIO21 / 40  (SPI1 SCLK)
  SDA -> GPIO20 / 38  (SPI1 MOSI)
  CS  -> GPIO18 / 12  (SPI1 CE0 = /dev/spidev1.0)
  DC  -> GPIO26 / 37  (任意GPIO)
  RES -> GPIO6  / 31  (任意GPIO)
  BL  -> GPIO13 / 33  (任意GPIO, PWMで明るさ制御。固定点灯ならVCC直結でも可)

注意:
- VCCは3.3V系を推奨。電池駆動ならラズパイのGNDと必ず共通化。
- 画面が白のまま: 配線ミス（特に DC/RES/CS）か GND共通化忘れが多い。
- 色入れ替わり(BGR問題): luma.lcdのrotateやRGB/BGR設定で調整（下のORDER参照）。
"""

import os
import sys
import time
import signal
import RPi.GPIO as GPIO
from PIL import Image, ImageDraw, ImageFont

from luma.core.interface.serial import spi
from luma.lcd.device import st7735
from luma.core.render import canvas

# ====== あなたのピン割り当て（必要ならここだけ変更） ======
SPI_PORT = 1       # SPI1
SPI_DEVICE = 0     # CE0 -> /dev/spidev1.0
PIN_DC = 26        # D/C
PIN_RST = 6        # RESET
PIN_BL = 13        # Backlight (PWM可能)
BUS_HZ = 16_000_000  # SPIクロック

# 表示サイズと配向（基板により横縦が異なることがある）
WIDTH = 128
HEIGHT = 160
ROTATE = 0   # 0, 1, 2, 3 で回転。必要に応じて変更。
# 色順序（基板でBGRのことがある）。"RGB" か "BGR"
ORDER = "RGB"  # もし色が入れ替わるなら "BGR" にする


def die(msg: str):
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def ensure_spidev():
    path = f"/dev/spidev{SPI_PORT}.{SPI_DEVICE}"
    if not os.path.exists(path):
        die(f"{path} が見つかりません。/boot(または /boot/firmware)/config.txt に "
            f"'dtoverlay=spi1-1cs' を追記して再起動してください。")


def setup_backlight():
    """バックライトをPWMで100%点灯（明るさ調整したい場合は duty を変える）。"""
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(PIN_BL, GPIO.OUT)
    bl = GPIO.PWM(PIN_BL, 1000)  # 1kHz
    bl.start(100)  # 100%点灯
    return bl


def init_device():
    serial = spi(
        port=SPI_PORT,
        device=SPI_DEVICE,
        gpio_DC=PIN_DC,
        gpio_RST=PIN_RST,
        bus_speed_hz=BUS_HZ,
    )
    dev = st7735(
        serial_interface=serial,
        width=WIDTH,
        height=HEIGHT,
        rotate=ROTATE,
        order=ORDER,  # "RGB" or "BGR"
    )
    return dev


def demo(device):
    """簡単な描画デモ（枠、テキスト、カラーグラデーション、動くバー）。"""
    # 1) 起動画面
    with canvas(device) as draw:
        draw.rectangle(device.bounding_box, outline="white", fill="black")
        draw.text((6, 6), "ST7735 SPI1 demo", fill="white")
        draw.text((6, 24), f"{WIDTH}x{HEIGHT} rotate={ROTATE} {ORDER}", fill="white")
        draw.text((6, 42), "GPIO: DC=26 RST=6 CS=18 SCLK=21 MOSI=20", fill="white")
    time.sleep(1.2)

    # 2) カラーグラデーション（縦方向）
    img = Image.new("RGB", (device.width, device.height))
    px = img.load()
    for y in range(device.height):
        r = int(255 * y / (device.height - 1))
        g = int(255 * (device.height - 1 - y) / (device.height - 1))
        b = 128
        for x in range(device.width):
            px[x, y] = (r, g, b)
    device.display(img)
    time.sleep(0.8)

    # 3) 動くバーと時計（無限ループ、Ctrl+Cで終了）
    font = ImageFont.load_default()
    t0 = time.time()
    try:
        while True:
            elapsed = time.time() - t0
            with canvas(device) as draw:
                # 枠
                draw.rectangle(device.bounding_box, outline="white", fill="black")
                # タイトル
                draw.text((4, 4), "ST7735 running on SPI1 (/dev/spidev1.0)", fill="white", font=font)
                # 動くバー
                bar_w = int((device.width - 8) * ((elapsed % 2.0) / 2.0))
                draw.rectangle((4, 22, 4 + bar_w, 40), outline="white", fill="white")
                # 時計
                draw.text((4, 48), time.strftime("%Y-%m-%d %H:%M:%S"), fill="white", font=font)
                # 画面サイズ表示
                draw.text((4, 62), f"{device.width}x{device.height} rotate={ROTATE} {ORDER}", fill="white", font=font)
            time.sleep(0.03)
    except KeyboardInterrupt:
        pass


def cleanup(pwm):
    try:
        if pwm is not None:
            pwm.stop()
    finally:
        GPIO.cleanup()


def main():
    ensure_spidev()
    bl_pwm = setup_backlight()
    dev = init_device()
    # Ctrl+C時にGPIO後始末
    signal.signal(signal.SIGINT, lambda s, f: cleanup(bl_pwm) or sys.exit(0))
    demo(dev)
    cleanup(bl_pwm)


if __name__ == "__main__":
    main()
