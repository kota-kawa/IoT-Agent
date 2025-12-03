# -*- coding: utf-8 -*-
# Raspberry Pi Pico + 16x2 LCD(HD44780互換) 4bit直結
# 文字は出さず、カスタム文字だけで「動く顔」を表示する
#
# 配線:
#   LCD1 VSS->GND, LCD2 VDD->5V(VBUS)
#   LCD3 V0 <- RC出力 (GP12 --10kΩ--> V0, V0 --0.1µF(〜1µF可)--> GND)
#   LCD4 RS->GP2, LCD5 RW->GND, LCD6 E->GP3
#   LCD11 D4->GP6, LCD12 D5->GP7, LCD13 D6->GP8, LCD14 D7->GP9
#   LCD15 A->5V(必要なら100〜220Ω直列), LCD16 K->GND
#
# 注意:
#  - Picoは3.3Vロジック。RWを必ずGND固定にしてPicoへ5Vを戻さない。
#  - 4bitのビット割り当ては bit0->D4, bit1->D5, bit2->D6, bit3->D7。
#  - 文字列は一切表示しない（スペース以外のASCIIは送らない）。

from machine import Pin, PWM
import time

# ===== 調整用パラメータ =====
# 45% -> おおよそ0.9V。顔が薄い場合は60%まで段階的に上げる。
CONTRAST_PERCENT_DEFAULT = 45
CONTRAST_PERCENT_MAX = 60
PWM_FREQ_HZ = 100_000

# LCDピン割り当て（要求: GP13/14/15は使わない）
LCD_RS = 2
LCD_E  = 3
LCD_D4 = 6
LCD_D5 = 7
LCD_D6 = 8
LCD_D7 = 9
CONTRAST_PWM_PIN = 12

# 顔の表示位置（左目の開始カラム）
FACE_COL = 4         # 0..15
FACE_ROW_TOP = 0     # 目の行(上段)

# ===== HD44780 4bitドライバ =====
class HD44780:
    def __init__(self, rs, e, d4, d5, d6, d7, cols=16, rows=2):
        self.rs = rs if isinstance(rs, Pin) else Pin(rs, Pin.OUT)
        self.e  = e  if isinstance(e,  Pin) else Pin(e,  Pin.OUT)
        self.data = [
            d4 if isinstance(d4, Pin) else Pin(d4, Pin.OUT),  # D4
            d5 if isinstance(d5, Pin) else Pin(d5, Pin.OUT),  # D5
            d6 if isinstance(d6, Pin) else Pin(d6, Pin.OUT),  # D6
            d7 if isinstance(d7, Pin) else Pin(d7, Pin.OUT),  # D7
        ]
        for p in [self.rs, self.e] + self.data:
            p.init(Pin.OUT); p.value(0)
        self.cols = cols
        self.rows = rows
        self._row_offsets = [0x00, 0x40, 0x00 + cols, 0x40 + cols]
        self._init_lcd()

    @staticmethod
    def _delay_us(us): time.sleep_us(us)

    def _pulse_enable(self):
        self.e.value(0); self._delay_us(1)
        self.e.value(1); self._delay_us(1)   # >450ns
        self.e.value(0); self._delay_us(50)

    def _write4bits(self, nibble):
        # bit0->D4, bit1->D5, bit2->D6, bit3->D7
        for i, p in enumerate(self.data):
            p.value((nibble >> i) & 0x01)
        self._pulse_enable()

    def _send(self, value, rs_mode):
        self.rs.value(1 if rs_mode else 0)
        self._write4bits((value >> 4) & 0x0F)  # 上位4bit
        self._write4bits(value & 0x0F)         # 下位4bit

    def command(self, cmd): self._send(cmd, 0)
    def write(self, val):   self._send(val, 1)

    def clear(self): self.command(0x01); time.sleep_ms(2)
    def home(self):  self.command(0x02); time.sleep_ms(2)

    def set_cursor(self, col, row):
        if row >= self.rows: row = self.rows - 1
        addr = 0x80 | (col + self._row_offsets[row])
        self.command(addr)

    def display_on(self, on=True, cursor=False, blink=False):
        ctrl = 0x08 | (0x04 if on else 0) | (0x02 if cursor else 0) | (0x01 if blink else 0)
        self.command(ctrl)

    def create_char(self, location, bitmap5x8):
        """location: 0..7, bitmap5x8: 8行ぶんの5bit値(下位5bit使用)"""
        location &= 0x07
        self.command(0x40 | (location << 3))  # CGRAM
        for row in range(8):
            self.write(bitmap5x8[row] & 0x1F)
        self.set_cursor(0, 0)  # DDRAMへ戻す

    def _init_lcd(self):
        time.sleep_ms(50)
        self.rs.value(0); self.e.value(0)
        # 4bitへ
        self._write4bits(0x03); time.sleep_ms(5)
        self._write4bits(0x03); self._delay_us(150)
        self._write4bits(0x03); self._delay_us(150)
        self._write4bits(0x02)
        # Function set: 4bit, 2line, 5x8
        self.command(0x28)
        self.command(0x08)   # display off
        self.clear()
        self.command(0x06)   # entry mode
        self.display_on(True, False, False)

# ===== コントラスト(PWM) =====
_pwm = PWM(Pin(CONTRAST_PWM_PIN))
_pwm.freq(PWM_FREQ_HZ)
def set_contrast_percent(percent):
    if percent < 0: percent = 0
    if percent > 100: percent = 100
    max_percent = float(CONTRAST_PERCENT_MAX)
    duty = int((percent * max_percent / 100.0) * 65535.0 / 100.0)
    _pwm.duty_u16(duty)

# ===== 顔アニメ（カスタム文字のみ）=====
def _row(bits):
    if isinstance(bits, str):
        bits = bits.replace('.', '0').replace('#', '1')
        return int(bits[:5], 2) & 0x1F
    return int(bits) & 0x1F

def eye_open(pupil="center"):
    base = [
        _row("00000"),
        _row("01110"),
        _row("10001"),
        _row("10001"),
        _row("10001"),
        _row("01110"),
        _row("00000"),
        _row("00000"),
    ]
    if pupil == "left":   base[3] |= _row("00010")
    elif pupil == "right":base[3] |= _row("01000")
    else:                 base[3] |= _row("00100")
    return base

def eye_closed():
    return [
        _row("00000"),
        _row("00000"),
        _row("00000"),
        _row("11111"),
        _row("00000"),
        _row("00000"),
        _row("00000"),
        _row("00000"),
    ]

def mouth_neutral_left():
    return [
        _row("00000"),
        _row("00000"),
        _row("00000"),
        _row("00000"),
        _row("00000"),
        _row("11111"),
        _row("00000"),
        _row("00000"),
    ]

def mouth_neutral_right(): return mouth_neutral_left()

def mouth_open_left():
    return [
        _row("00000"),
        _row("00000"),
        _row("11111"),
        _row("10001"),
        _row("10001"),
        _row("11111"),
        _row("00000"),
        _row("00000"),
    ]

def mouth_open_right(): return mouth_open_left()

def mouth_smile_left():
    return [
        _row("00000"),
        _row("00000"),
        _row("00000"),
        _row("00001"),
        _row("00011"),
        _row("00111"),
        _row("01110"),
        _row("00000"),
    ]

def mouth_smile_right():
    return [
        _row("00000"),
        _row("00000"),
        _row("00000"),
        _row("10000"),
        _row("11000"),
        _row("11100"),
        _row("01110"),
        _row("00000"),
    ]

class FaceAnimator:
    # CGRAMスロット
    EYE_L, EYE_R, MOUTH_L, MOUTH_R = 0, 1, 2, 3

    def __init__(self, lcd, col=FACE_COL, row_top=FACE_ROW_TOP):
        self.lcd = lcd
        self.col = col
        self.row_top = row_top
        self.row_bottom = row_top + 1
        self._init_slots()
        self._draw_face()

    def _init_slots(self):
        self.lcd.create_char(self.EYE_L, eye_open("center"))
        self.lcd.create_char(self.EYE_R, eye_open("center"))
        self.lcd.create_char(self.MOUTH_L, mouth_neutral_left())
        self.lcd.create_char(self.MOUTH_R, mouth_neutral_right())

    def _draw_face(self):
        # 目（中央2マス空ける）※空白はスペース=表示なし
        self.lcd.set_cursor(self.col, self.row_top)
        self.lcd.write(self.EYE_L)
        self.lcd.write(0x20)  # space
        self.lcd.write(0x20)  # space
        self.lcd.write(self.EYE_R)
        # 口（下段2マス）
        self.lcd.set_cursor(self.col, self.row_bottom)
        self.lcd.write(self.MOUTH_L)
        self.lcd.write(self.MOUTH_R)

    # 表情制御（文字は使わない）
    def eyes(self, where="center"):
        if where == "blink":
            self.lcd.create_char(self.EYE_L, eye_closed())
            self.lcd.create_char(self.EYE_R, eye_closed())
        else:
            self.lcd.create_char(self.EYE_L, eye_open(where))
            self.lcd.create_char(self.EYE_R, eye_open(where))

    def mouth(self, shape="neutral"):
        if shape == "open":
            self.lcd.create_char(self.MOUTH_L, mouth_open_left())
            self.lcd.create_char(self.MOUTH_R, mouth_open_right())
        elif shape == "smile":
            self.lcd.create_char(self.MOUTH_L, mouth_smile_left())
            self.lcd.create_char(self.MOUTH_R, mouth_smile_right())
        else:
            self.lcd.create_char(self.MOUTH_L, mouth_neutral_left())
            self.lcd.create_char(self.MOUTH_R, mouth_neutral_right())

    # アニメーション
    def animate_blink(self, dt_open=1500, dt_close=120):
        time.sleep_ms(dt_open)
        self.eyes("blink"); time.sleep_ms(dt_close)
        self.eyes("center")

    def animate_lookaround(self, dwell=220):
        for pos in ("left", "center", "right", "center"):
            self.eyes(pos); time.sleep_ms(dwell)

    def animate_talk(self, beats=10, tempo_ms=110):
        for i in range(beats):
            self.mouth("open" if i % 2 == 0 else "neutral")
            time.sleep_ms(tempo_ms)
        self.mouth("neutral")

# ===== メイン =====
def main():
    lcd = HD44780(LCD_RS, LCD_E, LCD_D4, LCD_D5, LCD_D6, LCD_D7, cols=16, rows=2)
    set_contrast_percent(CONTRAST_PERCENT_DEFAULT)
    time.sleep_ms(10)
    lcd.clear()

    face = FaceAnimator(lcd, col=FACE_COL, row_top=FACE_ROW_TOP)

    try:
        while True:
            face.animate_blink(dt_open=1500, dt_close=120)
            face.animate_lookaround(dwell=220)
            face.animate_talk(beats=10, tempo_ms=110)
            face.mouth("smile"); time.sleep_ms(600); face.mouth("neutral")
    except KeyboardInterrupt:
        # 終了時も文字は出さない
        lcd.clear()

if __name__ == "__main__":
    main()

