# -*- coding: utf-8 -*-
# Raspberry Pi Pico + 16x2 LCD(HD44780互換) 4bit直結
# つまみ無し: GP12のPWMをRC(10kΩ+0.1µF)で直流化してV0へ
#
# 配線:
#   LCD1 VSS->GND, LCD2 VDD->5V(VBUS), LCD3 V0<- RC出力 (GP12--10kΩ-->V0, V0--0.1µF-->GND)
#   LCD4 RS->GP2, LCD5 RW->GND, LCD6 E->GP3,
#   LCD11 D4->GP6, LCD12 D5->GP7, LCD13 D6->GP8, LCD14 D7->GP9
#   LCD15 A->5V(必要なら100~220Ω直列), LCD16 K->GND
#
# 注意:
#  - Picoは3.3Vロジック。RWは必ずGND固定に。
#  - コントラストは多くの個体で0.3~1.0Vが見やすい。下の定数で調整。

from machine import Pin, PWM
import time

# ------------ ユーザー調整用 定数 ------------
CONTRAST_PERCENT_DEFAULT = 15
PWM_FREQ_HZ = 100_000

# LCDピン割り当て（GP13/14/15は不使用）
LCD_RS = 2   # LCD pin4 RS
LCD_E  = 3   # LCD pin6 E
LCD_D4 = 6   # LCD pin11 D4
LCD_D5 = 7   # LCD pin12 D5
LCD_D6 = 8   # LCD pin13 D6
LCD_D7 = 9   # LCD pin14 D7

# コントラスト用PWMピン
CONTRAST_PWM_PIN = 12

# ------------ HD44780 4bitドライバ ------------
class HD44780:
    """HD44780-compatible character LCD (4-bit mode)."""
    def __init__(self, rs, e, d4, d5, d6, d7, cols=16, rows=2):
        self.rs = rs if isinstance(rs, Pin) else Pin(rs, Pin.OUT)
        self.e  = e  if isinstance(e,  Pin) else Pin(e,  Pin.OUT)
        # D4, D5, D6, D7 の順
        self.data = [
            d4 if isinstance(d4, Pin) else Pin(d4, Pin.OUT),
            d5 if isinstance(d5, Pin) else Pin(d5, Pin.OUT),
            d6 if isinstance(d6, Pin) else Pin(d6, Pin.OUT),
            d7 if isinstance(d7, Pin) else Pin(d7, Pin.OUT),
        ]
        for p in [self.rs, self.e] + self.data:
            p.init(Pin.OUT)
            p.value(0)

        self.cols = cols
        self.rows = rows
        self._row_offsets = [0x00, 0x40, 0x00 + cols, 0x40 + cols]
        self._init_lcd()

    @staticmethod
    def _delay_us(us):
        time.sleep_us(us)

    def _pulse_enable(self):
        self.e.value(0)
        self._delay_us(1)
        self.e.value(1)
        self._delay_us(1)   # >450ns
        self.e.value(0)
        self._delay_us(50)  # コマンド後待ち

    def _write4bits(self, nibble):
        # 【修正点】bit0->D4, bit1->D5, bit2->D6, bit3->D7 に出力する
        for i, p in enumerate(self.data):   # self.data = [D4, D5, D6, D7]
            p.value((nibble >> i) & 0x01)
        self._pulse_enable()

    def _send(self, value, rs_mode):
        self.rs.value(1 if rs_mode else 0)
        self._write4bits((value >> 4) & 0x0F)  # 上位4ビット
        self._write4bits(value & 0x0F)         # 下位4ビット

    # パブリックAPI
    def command(self, cmd):
        self._send(cmd, 0)

    def write(self, val):
        self._send(val, 1)

    def write_string(self, s: str):
        for ch in s:
            self.write(ord(ch))

    def clear(self):
        self.command(0x01)
        time.sleep_ms(2)

    def home(self):
        self.command(0x02)
        time.sleep_ms(2)

    def set_cursor(self, col, row):
        if row >= self.rows:
            row = self.rows - 1
        addr = 0x80 | (col + self._row_offsets[row])
        self.command(addr)

    def display_on(self, on=True, cursor=False, blink=False):
        ctrl = 0x08 | (0x04 if on else 0) | (0x02 if cursor else 0) | (0x01 if blink else 0)
        self.command(ctrl)

    # 初期化シーケンス
    def _init_lcd(self):
        time.sleep_ms(50)        # 電源安定待ち
        self.rs.value(0)
        self.e.value(0)

        # 4bitモードへ
        self._write4bits(0x03)
        time.sleep_ms(5)
        self._write4bits(0x03)
        self._delay_us(150)
        self._write4bits(0x03)
        self._delay_us(150)
        self._write4bits(0x02)

        # Function set: 4bit, 2行, 5x8
        self.command(0x28)
        # Display off
        self.command(0x08)
        # Clear
        self.clear()
        # Entry mode
        self.command(0x06)
        # Display ON
        self.display_on(True, False, False)

# ------------ コントラスト(PWM) ------------
_pwm = PWM(Pin(CONTRAST_PWM_PIN))
_pwm.freq(PWM_FREQ_HZ)

def set_contrast_percent(percent: int):
    if percent < 0:   percent = 0
    if percent > 100: percent = 100
    max_percent = 40.0  # 3.3Vの40% ≈ 1.32V
    duty_percent = percent * max_percent / 100.0
    duty_u16 = int(duty_percent * 65535.0 / 100.0)
    _pwm.duty_u16(duty_u16)

def set_contrast_volts(volts: float):
    if volts < 0.0: volts = 0.0
    if volts > 1.5: volts = 1.5
    _pwm.duty_u16(int((volts / 3.3) * 65535.0))

# ------------ メイン ------------
def main():
    lcd = HD44780(LCD_RS, LCD_E, LCD_D4, LCD_D5, LCD_D6, LCD_D7, cols=16, rows=2)

    set_contrast_percent(CONTRAST_PERCENT_DEFAULT)
    time.sleep_ms(10)

    lcd.clear()
    lcd.set_cursor(0, 0)
    lcd.write_string("Hello, Pico 1602")
    lcd.set_cursor(0, 1)
    lcd.write_string("Contrast=")
    lcd.write_string("{:>3d}%".format(CONTRAST_PERCENT_DEFAULT))

    # お好みで微調整
    for v in (10, 15, 20, 25, 30, 35):
        set_contrast_percent(v)
        lcd.set_cursor(10, 1)
        lcd.write_string("{:>3d}%".format(v))
        time.sleep_ms(400)
    set_contrast_percent(CONTRAST_PERCENT_DEFAULT)

if __name__ == "__main__":
    main()

