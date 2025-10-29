#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ST7735 1.8inch TFT (シルク: GND VCC SCL SDA RES DC CS BL)
Raspberry Pi 4 / SPI1 (CE0) で動作確認用デモ。

配線（BCM番号 / 物理ピン）:
  SCL -> GPIO21 / 40  (SPI1 SCLK)
  SDA -> GPIO20 / 38  (SPI1 MOSI)
  CS  -> GPIO18 / 12  (SPI1 CE0 = /dev/spidev1.0)
  DC  -> GPIO26 / 37  (任意GPIO)
  RES -> GPIO6  / 31  (任意GPIO)
  BL  -> GPIO13 / 33  (任意GPIO, PWMで明るさ制御。固定点灯ならVCC直結でも可)

注意:
- VCCは3.3V系を推奨。電池駆動時もラズパイのGNDと必ず共通化。
- 画面が白のまま: 配線ミス（特に DC/RES/CS）や GND未共通が多い。
- 色入れ替わり(BGR): BGR=True にする（下の BGR 参照）。
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

# ====== あなたのピン割り当て ======
SPI_PORT = 1        # SPI1
SPI_DEVICE = 0      # CE0 -> /dev/spidev1.0
PIN_DC = 26         # D/C (GPIO26)
PIN_RST = 6         # RESET (GPIO6)
PIN_BL = 13         # Backlight (GPIO13, PWM可)
BUS_HZ = 16_000_000 # SPIクロック

# ST7735 は「基準: 160x128（横置き）」が正解。縦にしたい場合は rotate で回転させる。
WIDTH  = 160
HEIGHT = 128
ROTATE = 1          # 0:回転なし, 1:90°CW, 2:180°, 3:270°
BGR = False         # 色が入れ替わるなら True に

def die(msg: str):
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)

def ensure_spidev():
    path = f"/dev/spidev{SPI_PORT}.{SPI_DEVICE}"
    if not os.path.exists(path):
        die(f"{path} が見つかりません。/boot(または /boot/firmware)/config.txt に "
            f"'dtoverlay=spi1-1cs' を追記して再起動してください。")

def setup_backlight():
    """バックライトPWMを開始（100%点灯）。"""
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(PIN_BL, GPIO.OUT)
    bl = GPIO.PWM(PIN_BL, 1000)  # 1kHz
    bl.start(100)                # 100% duty
    return bl

def init_device():
    serial_if = spi(
        port=SPI_PORT,
        device=SPI_DEVICE,
        gpio_DC=PIN_DC,
        gpio_RST=PIN_RST,
        bus_speed_hz=BUS_HZ,
    )
    dev = st7735(
        serial_interface=serial_if,
        width=WIDTH,
        height=HEIGHT,
        rotate=ROTATE,
        bgr=BGR,   # 必要なら True
    )
    return dev

def demo(device):
    """簡単デモ: 起動画面 -> グラデーション -> 動くバー&時計"""
    # 起動画面
    with canvas(device) as draw:
        draw.rectangle(device.bounding_box, outline="white", fill="black")
        draw.text((6, 6),  "ST7735 SPI1 demo", fill="white")
        draw.text((6, 22), f"{WIDTH}x{HEIGHT} rotate={ROTATE} bgr={BGR}", fill="white")
        draw.text((6, 38), "GPIO: DC=26 RST=6 CS=18 SCLK=21 MOSI=20", fill="white")
    time.sleep(1.0)

    # 縦グラデーション
    img = Image.new("RGB", (device.width, device.height))
    px = img.load()
    for y in range(device.height):
        r = int(255 * y / max(1, device.height - 1))
        g = int(255 * (device.height - 1 - y) / max(1, device.height - 1))
        b = 128
        for x in range(device.width):
            px[x, y] = (r, g, b)
    device.display(img)
    time.sleep(0.8)

    # 動くバー & 時計
    font = ImageFont.load_default()
    t0 = time.time()
    try:
        while True:
            elapsed = time.time() - t0
            with canvas(device) as draw:
                draw.rectangle(device.bounding_box, outline="white", fill="black")
                draw.text((4, 4), "ST7735 on SPI1 (/dev/spidev1.0)", fill="white", font=font)
                bar_w = int((device.width - 8) * ((elapsed % 2.0) / 2.0))
                draw.rectangle((4, 22, 4 + bar_w, 40), outline="white", fill="white")
                draw.text((4, 48), time.strftime("%Y-%m-%d %H:%M:%S"), fill="white", font=font)
                draw.text((4, 62), f"{device.width}x{device.height} rot={ROTATE} bgr={BGR}", fill="white", font=font)
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
    signal.signal(signal.SIGINT, lambda s, f: cleanup(bl_pwm) or sys.exit(0))
    demo(dev)
    cleanup(bl_pwm)

if __name__ == "__main__":
    main()
