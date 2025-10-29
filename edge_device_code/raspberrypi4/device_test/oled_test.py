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

本ファイルではバックライトPWMを gpiozero で制御します。
"""

import os
import sys
import time
import signal
from pathlib import Path
from typing import Optional

from gpiozero import PWMLED

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

# === グローバル参照（シグナルハンドラで使う） ===
_bl_pwm: Optional[PWMLED] = None
_device: Optional[st7735] = None


def die(msg: str):
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def ensure_spidev():
    """SPIデバイス (/dev/spidev{port}.{dev}) の存在を確認。"""
    path = Path(f"/dev/spidev{SPI_PORT}.{SPI_DEVICE}")
    if not path.exists():
        die(f"{path} が見つかりません。/boot(または /boot/firmware)/config.txt に "
            f"'dtoverlay=spi1-1cs' を追記して再起動してください。")


def setup_backlight() -> PWMLED:
    """
    バックライトPWMを開始（100%点灯）。
    gpiozero.PWMLED を使用。duty は 0.0〜1.0。
    """
    # frequency=1000 で 1kHz。初期値を 1.0（=100%）に設定。
    bl = PWMLED(PIN_BL, frequency=1000, active_high=True, initial_value=1.0)
    return bl


def init_device():
    """
    ST7735 デバイス初期化。
    DC/RST は luma 側が制御します（数値でピンを指定）。
    """
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


def demo(device: st7735):
    """簡単デモ: 起動画面 -> グラデーション -> 動くバー&時計"""
    # 起動画面
    with canvas(device) as draw:
        draw.rectangle(device.bounding_box, outline="white", fill="black")
        draw.text((6, 6),  "ST7735 SPI1 demo (gpiozero BL)", fill="white")
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
        # SIGINT で抜ける（後続で cleanup 実行）
        pass


def cleanup():
    """リソース開放（gpiozero PWMLED を close）。"""
    global _bl_pwm
    try:
        if _bl_pwm is not None:
            # 値を 0 に落としてからクローズ（任意）
            try:
                _bl_pwm.value = 0.0
            except Exception:
                pass
            _bl_pwm.close()
    finally:
        _bl_pwm = None


def _sigint_handler(signum, frame):
    """Ctrl-C などで呼ばれるシグナルハンドラ。"""
    cleanup()
    # luma 側は with canvas を抜ければ問題ない想定
    sys.exit(0)


def main():
    global _bl_pwm, _device

    ensure_spidev()

    # バックライト（gpiozero）初期化
    _bl_pwm = setup_backlight()

    # デバイス初期化
    _device = init_device()

    # Ctrl-C で安全に終了
    signal.signal(signal.SIGINT, _sigint_handler)

    # デモ実行
    try:
        demo(_device)
    finally:
        cleanup()


if __name__ == "__main__":
    main()
