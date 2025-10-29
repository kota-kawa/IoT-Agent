#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Robot Face animation for ST7735 (1.8inch 160x128) on Raspberry Pi 4
- 横向き（ランドスケープ）表示
- まばたき / 視線移動 / 口パク の簡易アニメーション

配線（BCM番号 / 物理ピン）:
  SCL -> GPIO21 / 40  (SPI1 SCLK)
  SDA -> GPIO20 / 38  (SPI1 MOSI)
  CS  -> GPIO18 / 12  (SPI1 CE0 -> /dev/spidev1.0)
  DC  -> GPIO26 / 37  (任意GPIO)
  RES -> GPIO6  / 31  (任意GPIO)
  BL  -> GPIO13 / 33  (任意GPIO, PWMで明るさ制御。固定点灯ならVCC直結でも可)

注意:
- VCC は 3.3V系推奨。電池駆動の場合も Raspberry Pi の GND と必ず共通化。
- 右端に細い線が出る場合は h_offset / v_offset を 1〜2 で微調整。
- 本スクリプトはバックライト(PWM)を gpiozero で制御します（RPi.GPIO 非使用）。
"""

import os
import sys
import math
import time
import signal
from typing import Optional

from gpiozero import PWMLED
from PIL import Image, ImageDraw, ImageFont

from luma.core.interface.serial import spi
from luma.lcd.device import st7735
from luma.core.render import canvas

# ====== ハード設定（あなたの配線に合わせてある） ======
SPI_PORT   = 1        # SPI1
SPI_DEVICE = 0        # CE0 -> /dev/spidev1.0
PIN_DC     = 26
PIN_RST    = 6
PIN_BL     = 13
BUS_HZ     = 16_000_000

# ST7735 は基準が 160x128（横向き）
WIDTH, HEIGHT = 160, 128
ROTATE = 0           # 0=横向きのまま
BGR    = False       # 赤青が入れ替わるなら True
H_OFF, V_OFF = 0, 0  # 右端の線対策に 1〜2 を試す

# ====== デザイン色 ======
COL_BG       = (12, 18, 26)      # 背景
COL_PANEL    = (20, 30, 42)      # 顔パネル
COL_FRAME    = (220, 220, 230)   # 枠線
COL_EYE      = (235, 235, 245)   # 白目
COL_PUPIL    = (30, 40, 55)      # 黒目
COL_MOUTH    = (120, 200, 255)   # 口
COL_ACCENT   = (80, 140, 255)    # アクセント
COL_TEXT     = (230, 230, 240)

# ====== アニメのパラメータ ======
FPS            = 50
BLINK_PERIOD   = 3.2   # まばたき周期（秒）
BLINK_LEN      = 0.14  # 目閉じ持続（秒）
EYE_H_SWEEP    = 10    # 瞳の左右移動量（px）
EYE_V_SWEEP    = 2     # 瞳の上下移動量（px）
EYE_OPEN_BASE  = 1.00  # 通常の目の開き倍率
MOUTH_OPEN_MAX = 14    # 口の最大開き（px）
BREATH_PERIOD  = 5.0   # 「呼吸」っぽい上下揺れ（顔全体を1〜2px）

# ====== グローバル（シグナルハンドラで参照） ======
_bl_pwm: Optional[PWMLED] = None


# ====== ユーティリティ ======
def die(msg: str):
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)

def ensure_spidev():
    path = f"/dev/spidev{SPI_PORT}.{SPI_DEVICE}"
    if not os.path.exists(path):
        die(f"{path} が見つかりません。/boot(または /boot/firmware)/config.txt に "
            f"'dtoverlay=spi1-1cs' を追記して再起動してください。")

def setup_backlight() -> PWMLED:
    """
    バックライトPWMを開始。gpiozero の PWMLED を使用（duty=0.0〜1.0）。
    frequency=1000 で 1kHz。初期は 1.0（100%）に設定。
    """
    bl = PWMLED(PIN_BL, frequency=1000, active_high=True, initial_value=1.0)
    return bl

def set_backlight_percent(bl: PWMLED, percent: float):
    """
    0〜100(%) を PWMLED.value(0.0〜1.0) に変換して設定。
    """
    p = max(0.0, min(100.0, percent))
    bl.value = p / 100.0

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
        bgr=BGR,
        h_offset=H_OFF,
        v_offset=V_OFF,
    )
    return dev

# ====== 描画関数 ======
def triangle_wave(t: float, period: float, lo: float, hi: float) -> float:
    """[lo, hi] の三角波"""
    x = (t % period) / period
    if x < 0.5:
        y = x * 2.0
    else:
        y = 2.0 - x * 2.0
    return lo + (hi - lo) * y

def blink_open_ratio(t: float) -> float:
    """
    まばたきの開き比 (0=閉じ, 1=全開) を返す。
    BLINK_LEN だけ完全に閉じ、その他は 1.0
    """
    phase = t % BLINK_PERIOD
    if phase < BLINK_LEN:
        # 完全に閉じる → 少し滑らかに
        r = max(0.0, 1.0 - (phase / BLINK_LEN) * 1.5)
        return r
    else:
        return 1.0

def draw_face(draw: ImageDraw.ImageDraw, t: float, W: int, H: int):
    """ロボット顔を描画"""
    # パネル（中央に丸角矩形風）
    margin = 4
    draw.rectangle((0, 0, W, H), fill=COL_BG)
    draw.rectangle((margin, margin, W - margin, H - margin), outline=COL_FRAME, fill=COL_PANEL, width=2)

    # 「呼吸」で微妙に上下揺れ
    breath = int(math.sin(2*math.pi * t / BREATH_PERIOD) * 1.5)

    # 目のレイアウト
    eye_w, eye_h = 42, 28
    eye_spacing = 18
    eye_y = 36 + breath
    left_eye_x  = W//2 - eye_spacing//2 - eye_w
    right_eye_x = W//2 + eye_spacing//2

    # 目の開き（まばたき）
    open_ratio = EYE_OPEN_BASE * blink_open_ratio(t)
    open_ratio = max(0.0, min(1.0, open_ratio))

    # 瞳の移動（左右+上下）
    px_off = int(math.sin(t * 1.4) * EYE_H_SWEEP)
    py_off = int(math.sin(t * 1.9) * EYE_V_SWEEP)

    # 目（白目）
    for ex in (left_eye_x, right_eye_x):
        # もとの楕円ボックス
        ebox = (ex, eye_y, ex + eye_w, eye_y + eye_h)
        # まず白目
        draw.ellipse(ebox, fill=COL_EYE, outline=COL_FRAME, width=2)

        # 開き制御: 上下から黒い「まぶた」を被せる
        if open_ratio < 1.0:
            close_amt = int((1.0 - open_ratio) * (eye_h // 2))
            # 上まぶた
            draw.rectangle((ex-2, eye_y-2, ex + eye_w + 2, eye_y + close_amt), fill=COL_PANEL)
            # 下まぶた
            draw.rectangle((ex-2, eye_y + eye_h - close_amt, ex + eye_w + 2, eye_y + eye_h + 2), fill=COL_PANEL)

        # 黒目（瞳）
        pr = 9
        cx = ex + eye_w//2 + px_off
        cy = eye_y + eye_h//2 + py_off
        # 白目をはみ出さないようにクリップ風に描画（簡易的に枠内判断）
        cx = max(ex + pr + 3, min(ex + eye_w - pr - 3, cx))
        cy = max(eye_y + pr + 3, min(eye_y + eye_h - pr - 3, cy))
        draw.ellipse((cx - pr, cy - pr, cx + pr, cy + pr), fill=COL_PUPIL, outline=None)

    # 口：横バーを口パクさせる
    mouth_w = 80
    mouth_h_base = 6
    mouth_x = (W - mouth_w)//2
    mouth_y = 88 + breath

    # 口の開き（サイン波ベース、時々大きく開ける動き）
    mouth_open = int((math.sin(t * 2.2) * 0.5 + 0.5) * MOUTH_OPEN_MAX)
    # ときどき「あくび」っぽく広めに
    if int(t) % 11 in (0, 1):
        mouth_open = max(mouth_open, MOUTH_OPEN_MAX - 2)

    # 外枠
    draw.rectangle((mouth_x - 2, mouth_y - 10, mouth_x + mouth_w + 2, mouth_y + 18),
                   outline=COL_FRAME, width=1)
    # 口（上唇・下唇の間を塗る）
    top = mouth_y - mouth_open // 2
    bottom = mouth_y + mouth_open // 2 + mouth_h_base
    draw.rectangle((mouth_x, top, mouth_x + mouth_w, bottom), fill=COL_MOUTH)

    # サイドメーター（アクセント）
    meter_h = 64
    meter_w = 6
    meter_y = 28 + breath
    # 左
    lv = int(triangle_wave(t, 1.6, 8, meter_h-8))
    draw.rectangle((margin + 2, meter_y, margin + 2 + meter_w, meter_y + meter_h), outline=COL_FRAME, width=1)
    draw.rectangle((margin + 3, meter_y + meter_h - lv, margin + 1 + meter_w, meter_y + meter_h - 1),
                   fill=COL_ACCENT)
    # 右
    rv = int(triangle_wave(t + 0.7, 1.9, 8, meter_h-8))
    draw.rectangle((W - margin - 2 - meter_w, meter_y, W - margin - 2, meter_y + meter_h),
                   outline=COL_FRAME, width=1)
    draw.rectangle((W - margin - 1 - meter_w, meter_y + meter_h - rv, W - margin - 3, meter_y + meter_h - 1),
                   fill=COL_ACCENT)

    # タイトル（左上に小さく）
    draw.text((6, 4), "Robot Face", fill=COL_TEXT, font=ImageFont.load_default())

# ====== メインループ ======
def main():
    global _bl_pwm

    ensure_spidev()
    _bl_pwm = setup_backlight()
    dev = init_device()

    # Ctrl+C / SIGTERM 終了でバックライトを確実に開放
    def _cleanup(*_):
        try:
            if _bl_pwm is not None:
                # 消灯してからクローズ（任意）
                try:
                    _bl_pwm.value = 0.0
                except Exception:
                    pass
                _bl_pwm.close()
        finally:
            sys.exit(0)

    signal.signal(signal.SIGINT, _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)

    t0 = time.time()
    frame_delay = 1.0 / FPS

    # 起動アニメ（フェードインっぽく）
    # gpiozero では 0.0〜1.0 を PWMLED.value に設定する
    for duty in (20, 40, 60, 80, 100):
        set_backlight_percent(_bl_pwm, duty)
        with canvas(dev) as draw:
            draw.rectangle((0, 0, dev.width, dev.height), fill=COL_BG)
            draw.text((10, dev.height//2 - 6), "Starting robot face...", fill=COL_TEXT)
        time.sleep(0.05)

    # メインアニメ
    while True:
        t = time.time() - t0
        with canvas(dev) as draw:
            draw_face(draw, t, dev.width, dev.height)
        # FPS 調整
        time.sleep(frame_delay)

if __name__ == "__main__":
    main()
