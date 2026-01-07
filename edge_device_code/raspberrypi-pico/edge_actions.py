from edge_compat import Pin, ADC, PWM, random, time

# =========================
# ハードウェア初期化
# =========================
led0 = Pin(0, Pin.OUT)
led1 = Pin(1, Pin.OUT)
TEMP_ADC = ADC(4)
ADC_TO_VOLT = 3.3 / 65535.0

# device_test/* と同じピン配置
BUZZER_PIN = 16  # パッシブブザー（GPIO16）
PIN_ENABLE = 13  # モータードライバ PWM
PIN_IN_1 = 14   # モータードライバ IN1
PIN_IN_2 = 15   # モータードライバ IN2

LCD_RS = 2
LCD_E = 3
LCD_D4 = 6
LCD_D5 = 7
LCD_D6 = 8
LCD_D7 = 9
CONTRAST_PWM_PIN = 12
CONTRAST_PERCENT_DEFAULT = 30  # ≈0.9V on V0 (3.3Vの約27%)
CONTRAST_PERCENT_MAX = 60      # 上げすぎると白反転するので60%に制限
PWM_FREQ_HZ = 100_000
LCD_COLS = 16
LCD_ROWS = 2
FACE_COL = 4
FACE_ROW_TOP = 0

# =========================
# デバイス提供関数
# =========================

def read_temperature(samples: int = 16, sample_interval_sec: float = 0.01):
    """内蔵温度センサ(ADC4)の平均推定温度(℃)"""
    if samples < 1:
        raise ValueError("samples must be >= 1")
    volts_sum = 0.0
    for _ in range(samples):
        reading = TEMP_ADC.read_u16()
        volts_sum += reading * ADC_TO_VOLT
        if sample_interval_sec > 0:
            time.sleep(sample_interval_sec)
    vtemp = volts_sum / samples
    temp_c = 27.0 - (vtemp - 0.706) / 0.001721
    print("[temp] est -> {:.2f} C (avg of {})".format(temp_c, samples))
    return round(temp_c, 2)


def led0_on():
    """Turn on LED 0 (GPIO0)"""
    led0.value(1)
    print("[led0] on")
    return True


def led0_off():
    """Turn off LED 0 (GPIO0)"""
    led0.value(0)
    print("[led0] off")
    return True


def led1_on():
    """Turn on LED 1 (GPIO1)"""
    led1.value(1)
    print("[led1] on")
    return True


def led1_off():
    """Turn off LED 1 (GPIO1)"""
    led1.value(0)
    print("[led1] off")
    return True


# =========================
# ブザー（パッシブ）制御
# =========================
class PassiveBuzzer:
    """PWMパッシブブザー制御（device_test/buzzer.py を簡略化）"""

    def __init__(self, pin: int = BUZZER_PIN):
        self.pin_no = pin
        self.pin = Pin(pin, Pin.OUT)
        self.pwm = PWM(self.pin)
        self.pwm.duty_u16(0)
        self._stopped = False

    def tone(self, freq_hz: int, duration_ms: int, duty: float = 0.5):
        if freq_hz <= 0 or duration_ms <= 0:
            self.silence(duration_ms)
            return
        duty = 0.0 if duty is None else max(0.0, min(1.0, float(duty)))
        duty_u16 = int(65535 * duty) or 1
        self.pwm.freq(int(freq_hz))
        self.pwm.duty_u16(duty_u16)
        time.sleep_ms(int(duration_ms))
        self.pwm.duty_u16(0)

    def silence(self, duration_ms: int):
        self.pwm.duty_u16(0)
        if duration_ms > 0:
            time.sleep_ms(int(duration_ms))

    def play(self, sequence):
        for item in sequence:
            if isinstance(item, (list, tuple)):
                if len(item) == 2:
                    freq, ms = item
                    duty = 0.5
                else:
                    freq, ms, duty = item[0], item[1], item[2]
                self.tone(int(freq), int(ms), duty)
            else:
                self.silence(int(item))

    def stop(self):
        if self._stopped:
            return
        try:
            self.pwm.duty_u16(0)
        except Exception:
            pass
        try:
            self.pwm.deinit()
        except Exception:
            pass
        try:
            self.pin = Pin(self.pin_no, Pin.OUT)
            self.pin.value(0)
        except Exception:
            pass
        self._stopped = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.stop()
        return False


def buzzer_tone(freq_hz: int = 2000, duration_ms: int = 300, duty: float = 0.5):
    """単発トーン（ブザー）"""
    if freq_hz <= 0:
        raise ValueError("freq_hz must be > 0")
    if duration_ms <= 0:
        raise ValueError("duration_ms must be > 0")
    with PassiveBuzzer(BUZZER_PIN) as buz:
        buz.tone(int(freq_hz), int(duration_ms), duty)
    print("[buzzer] played {} Hz for {} ms (duty {:.2f})".format(freq_hz, duration_ms, duty))
    return {"freq_hz": int(freq_hz), "duration_ms": int(duration_ms), "duty": float(duty)}


def buzzer_demo():
    """ドレミ＋ビープの簡易デモ"""
    seq = [
        (261, 200), (294, 200), (329, 200), (349, 200),
        (392, 200), (440, 200), (494, 200), (523, 200),
        200,
        (523, 200), (494, 200), (440, 200), (392, 200),
        (349, 200), (329, 200), (294, 200), (261, 400),
        300,
        (2000, 300, 0.5),
    ]
    with PassiveBuzzer(BUZZER_PIN) as buz:
        buz.play(seq)
    print("[buzzer] demo sequence complete")
    return True


def buzzer_pattern(pattern: str = "notify", duty: float = 0.4):
    """
    プリセットパターンでブザーを鳴らす。pattern: notify/alarm/confirm/fail/doorbell。
    """
    pattern = (pattern or "notify").lower()
    duty = 0.0 if duty is None else max(0.0, min(1.0, float(duty)))
    presets = {
        "notify": [(1200, 120, duty), 80, (1500, 120, duty)],
        "alarm": [(1800, 220, duty), 60, (1800, 220, duty), 60, (1800, 220, duty)],
        "confirm": [(1000, 90, duty), 40, (1400, 140, duty)],
        "fail": [(400, 180, duty * 0.8), 60, (320, 260, duty * 0.8)],
        "doorbell": [(880, 250, duty), 80, (1180, 350, duty)],
    }
    seq = presets.get(pattern)
    if seq is None:
        raise ValueError("unknown pattern: {}".format(pattern))
    with PassiveBuzzer(BUZZER_PIN) as buz:
        buz.play(seq)
    print("[buzzer] pattern played ({})".format(pattern))
    return {"pattern": pattern, "duty": duty}


# =========================
# モーター（PWM + IN1/IN2）制御
# =========================
_motor_in1 = None
_motor_in2 = None
_motor_pwm = None
_MOTOR_INITIALISED = False
_PWM_RANGE_MAX = 4095
_U16_MAX = 65535
_MOTOR_DEFAULT_SPEED = 3000  # high enough to overcome startup friction
_MOTOR_MIN_START_SPEED = 2000  # below this many motors will not start reliably


def _init_motor():
    global _motor_in1, _motor_in2, _motor_pwm, _MOTOR_INITIALISED
    if _MOTOR_INITIALISED:
        return
    _motor_in1 = Pin(PIN_IN_1, Pin.OUT)
    _motor_in2 = Pin(PIN_IN_2, Pin.OUT)
    _motor_pwm = PWM(Pin(PIN_ENABLE))
    _motor_pwm.freq(25_000)
    _motor_pwm.duty_u16(0)
    _motor_in1.value(0)
    _motor_in2.value(0)
    _MOTOR_INITIALISED = True


def _scale_12bit_to_u16(speed_0_4095: int) -> int:
    if speed_0_4095 < 0:
        speed_0_4095 = 0
    elif speed_0_4095 > _PWM_RANGE_MAX:
        speed_0_4095 = _PWM_RANGE_MAX
    duty = int((speed_0_4095 * _U16_MAX) / _PWM_RANGE_MAX + 0.5)
    if duty < 0:
        duty = 0
    elif duty > _U16_MAX:
        duty = _U16_MAX
    return duty


def SetMotorSpeed(direction_forward: bool, speed: int) -> None:
    """direction_forward=True でIN1=H/IN2=L。speedは0..4095想定。"""
    _init_motor()
    if direction_forward:
        _motor_in1.value(1)
        _motor_in2.value(0)
    else:
        _motor_in1.value(0)
        _motor_in2.value(1)
    if speed < 0:
        duty = 0
    elif speed > _PWM_RANGE_MAX:
        duty = _U16_MAX
    else:
        duty = _scale_12bit_to_u16(speed)
    _motor_pwm.duty_u16(duty)


def stop_motor():
    _init_motor()
    _motor_pwm.duty_u16(0)


def brake_motor():
    _init_motor()
    _motor_in1.value(1)
    _motor_in2.value(1)
    _motor_pwm.duty_u16(0)


def activate_propeller(direction: str = "forward", wind_power: int = _MOTOR_DEFAULT_SPEED, duration_ms: int = 1500, brake: bool = False):
    """
    プロペラを回転させて風を送る。direction: forward/reverse, wind_power:0..4095。
    duration_ms<=0 の場合は即座に停止（またはブレーキ）のみ行う。
    """
    print("[propeller_debug] received: dir={}, wind_power={}, duration_ms={}".format(direction, wind_power, duration_ms))
    direction = (direction or "").lower().strip()
    if direction not in ("forward", "reverse"):
        raise ValueError("direction must be 'forward' or 'reverse'")
    power_int = int(wind_power)
    if power_int < 0:
        power_int = 0
    elif power_int > _PWM_RANGE_MAX:
        power_int = _PWM_RANGE_MAX
    # トルク不足で回らないケースを避けるため、0でないのに小さすぎる出力は底上げする
    if power_int > 0 and power_int < _MOTOR_MIN_START_SPEED:
        print("[propeller] requested power {} too low; raising to {}".format(wind_power, _MOTOR_MIN_START_SPEED))
        power_int = _MOTOR_MIN_START_SPEED
    duration_ms_int = int(duration_ms)
    SetMotorSpeed(direction == "forward", power_int)
    if duration_ms_int > 0:
        time.sleep_ms(duration_ms_int)
    if brake:
        brake_motor()
    else:
        stop_motor()
    print("[propeller] dir={} power={} duration={}ms brake={}".format(direction, power_int, duration_ms_int, brake))
    return {
        "direction": direction,
        "wind_power": power_int,
        "duration_ms": duration_ms_int,
        "brake": bool(brake),
    }


# =========================
# LCDフェイス表示（HD44780）
# =========================
class HD44780:
    def __init__(self, rs, e, d4, d5, d6, d7, cols=16, rows=2):
        self.rs = rs if isinstance(rs, Pin) else Pin(rs, Pin.OUT)
        self.e = e if isinstance(e, Pin) else Pin(e, Pin.OUT)
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
        self._delay_us(1)
        self.e.value(0)
        self._delay_us(50)

    def _write4bits(self, nibble):
        for i, p in enumerate(self.data):
            p.value((nibble >> i) & 0x01)
        self._pulse_enable()

    def _send(self, value, rs_mode):
        self.rs.value(1 if rs_mode else 0)
        self._write4bits((value >> 4) & 0x0F)
        self._write4bits(value & 0x0F)

    def command(self, cmd):
        self._send(cmd, 0)

    def write(self, val):
        self._send(val, 1)

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

    def create_char(self, location, bitmap5x8):
        location &= 0x07
        self.command(0x40 | (location << 3))
        for row in range(8):
            self.write(bitmap5x8[row] & 0x1F)
        self.set_cursor(0, 0)

    def _init_lcd(self):
        time.sleep_ms(50)
        self.rs.value(0)
        self.e.value(0)
        self._write4bits(0x03)
        time.sleep_ms(5)
        self._write4bits(0x03)
        self._delay_us(150)
        self._write4bits(0x03)
        self._delay_us(150)
        self._write4bits(0x02)
        self.command(0x28)
        self.command(0x08)
        self.clear()
        self.command(0x06)
        self.display_on(True, False, False)


_contrast_pwm = PWM(Pin(CONTRAST_PWM_PIN))
_contrast_pwm.freq(PWM_FREQ_HZ)


def set_contrast_percent(percent):
    if percent < 0:
        percent = 0
    if percent > 100:
        percent = 100
    max_percent = float(CONTRAST_PERCENT_MAX)
    duty = int((percent * max_percent / 100.0) * 65535.0 / 100.0)
    _contrast_pwm.duty_u16(duty)


def _sanitize_text(text):
    if text is None:
        return ""
    cleaned = []
    for ch in str(text):
        code = ord(ch)
        cleaned.append(ch if 32 <= code <= 126 else " ")
    return "".join(cleaned)


def _prepare_lcd_line(text, max_len=LCD_COLS):
    line = _sanitize_text(text)
    if len(line) > max_len:
        line = line[:max_len]
    return line


def _row(bits):
    if isinstance(bits, str):
        bits = bits.replace(".", "0").replace("#", "1")
        return int(bits[:5], 2) & 0x1F
    return int(bits) & 0x1F


def eye_open(pupil="center", wide=False):
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
    if wide:
        base[1] = _row("11111")
        base[5] = _row("11111")
    if pupil == "left":
        base[3] |= _row("00010")
    elif pupil == "right":
        base[3] |= _row("01000")
    else:
        base[3] |= _row("00100")
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


def eye_sleepy():
    return [
        _row("00000"),
        _row("00000"),
        _row("11111"),
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


def mouth_neutral_right():
    return mouth_neutral_left()


def mouth_frown_left():
    return [
        _row("00000"),
        _row("00000"),
        _row("01110"),
        _row("00100"),
        _row("00010"),
        _row("00000"),
        _row("00000"),
        _row("00000"),
    ]


def mouth_frown_right():
    return [
        _row("00000"),
        _row("00000"),
        _row("01110"),
        _row("01000"),
        _row("10000"),
        _row("00000"),
        _row("00000"),
        _row("00000"),
    ]


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


def mouth_open_right():
    return mouth_open_left()


def mouth_o_left():
    return [
        _row("00000"),
        _row("00110"),
        _row("01001"),
        _row("01001"),
        _row("01001"),
        _row("00110"),
        _row("00000"),
        _row("00000"),
    ]


def mouth_o_right():
    return [
        _row("00000"),
        _row("01100"),
        _row("10010"),
        _row("10010"),
        _row("10010"),
        _row("01100"),
        _row("00000"),
        _row("00000"),
    ]


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


def mouth_grin_left():
    return [
        _row("00000"),
        _row("00000"),
        _row("00000"),
        _row("00011"),
        _row("00111"),
        _row("01111"),
        _row("01110"),
        _row("00000"),
    ]


def mouth_grin_right():
    return [
        _row("00000"),
        _row("00000"),
        _row("00000"),
        _row("11000"),
        _row("11100"),
        _row("11110"),
        _row("01110"),
        _row("00000"),
    ]


class FaceAnimator:
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
        self.lcd.set_cursor(self.col, self.row_top)
        self.lcd.write(self.EYE_L)
        self.lcd.write(0x20)
        self.lcd.write(0x20)
        self.lcd.write(self.EYE_R)
        self.lcd.set_cursor(self.col, self.row_bottom)
        self.lcd.write(self.MOUTH_L)
        self.lcd.write(self.MOUTH_R)

    def eyes(self, where="center"):
        where = (where or "center").lower()
        if where == "blink":
            left = eye_closed()
            right = eye_closed()
        elif where == "wink_left":
            left = eye_closed()
            right = eye_open("center")
        elif where == "wink_right":
            left = eye_open("center")
            right = eye_closed()
        elif where == "sleepy":
            left = eye_sleepy()
            right = eye_sleepy()
        elif where == "wide":
            left = eye_open("center", wide=True)
            right = eye_open("center", wide=True)
        else:
            left = eye_open(where)
            right = eye_open(where)
        self.lcd.create_char(self.EYE_L, left)
        self.lcd.create_char(self.EYE_R, right)

    def mouth(self, shape="neutral"):
        shape = (shape or "neutral").lower()
        if shape == "open":
            self.lcd.create_char(self.MOUTH_L, mouth_open_left())
            self.lcd.create_char(self.MOUTH_R, mouth_open_right())
        elif shape == "smile":
            self.lcd.create_char(self.MOUTH_L, mouth_smile_left())
            self.lcd.create_char(self.MOUTH_R, mouth_smile_right())
        elif shape == "grin":
            self.lcd.create_char(self.MOUTH_L, mouth_grin_left())
            self.lcd.create_char(self.MOUTH_R, mouth_grin_right())
        elif shape == "frown":
            self.lcd.create_char(self.MOUTH_L, mouth_frown_left())
            self.lcd.create_char(self.MOUTH_R, mouth_frown_right())
        elif shape == "o":
            self.lcd.create_char(self.MOUTH_L, mouth_o_left())
            self.lcd.create_char(self.MOUTH_R, mouth_o_right())
        else:
            self.lcd.create_char(self.MOUTH_L, mouth_neutral_left())
            self.lcd.create_char(self.MOUTH_R, mouth_neutral_right())

    def expression(self, name="happy"):
        name = (name or "neutral").lower()
        if name == "happy":
            self.eyes("center")
            self.mouth("smile")
        elif name == "wink":
            self.eyes("wink_left")
            self.mouth("smile")
        elif name == "surprised":
            self.eyes("wide")
            self.mouth("o")
        elif name == "sad":
            self.eyes("center")
            self.mouth("frown")
        elif name == "sleepy":
            self.eyes("sleepy")
            self.mouth("neutral")
        elif name == "annoyed":
            self.eyes("left")
            self.mouth("frown")
        elif name == "grin":
            self.eyes("center")
            self.mouth("grin")
        else:
            self.eyes("center")
            self.mouth("neutral")

    def animate_blink(self, dt_open=1500, dt_close=120):
        time.sleep_ms(int(dt_open))
        self.eyes("blink")
        time.sleep_ms(int(dt_close))
        self.eyes("center")

    def animate_lookaround(self, dwell=220):
        for pos in ("left", "center", "right", "center"):
            self.eyes(pos)
            time.sleep_ms(int(dwell))

    def animate_talk(self, beats=10, tempo_ms=110):
        for i in range(int(beats)):
            if i % 2 == 0:
                self.mouth("open")
            else:
                self.mouth("neutral")
            time.sleep_ms(int(tempo_ms))
        self.mouth("neutral")


def lcd_text(line1: str = "", line2: str = "", contrast_percent: int = CONTRAST_PERCENT_DEFAULT, duration_ms: int = 0):
    """
    任意の文字列をLCDに表示。ASCIIのみ、1行あたり最大16文字。duration_ms>0なら表示後に消去。
    """
    set_contrast_percent(contrast_percent)
    time.sleep_ms(10)  # RCフィルタが収束するまで待つ
    lcd = HD44780(LCD_RS, LCD_E, LCD_D4, LCD_D5, LCD_D6, LCD_D7, cols=LCD_COLS, rows=LCD_ROWS)
    line1_clean = _prepare_lcd_line(line1)
    line2_clean = _prepare_lcd_line(line2)
    duration_ms_int = int(duration_ms or 0)
    if duration_ms_int < 0:
        duration_ms_int = 0
    lcd.clear()
    lcd.set_cursor(0, 0)
    for ch in line1_clean:
        lcd.write(ord(ch))
    lcd.set_cursor(0, 1)
    for ch in line2_clean:
        lcd.write(ord(ch))
    if duration_ms_int > 0:
        time.sleep_ms(duration_ms_int)
        lcd.clear()
    print("[lcd] text displayed line1={!r} line2={!r} contrast={} duration_ms={}".format(
        line1_clean, line2_clean, contrast_percent, duration_ms_int
    ))
    return {
        "line1": line1_clean,
        "line2": line2_clean,
        "contrast_percent": contrast_percent,
        "duration_ms": duration_ms_int,
    }


def lcd_face(mode: str = "blink_cycle", contrast_percent: int = CONTRAST_PERCENT_DEFAULT):
    """
    LCDへ顔アニメーションを1サイクル表示。
    """
    set_contrast_percent(contrast_percent)
    time.sleep_ms(10)
    lcd = HD44780(LCD_RS, LCD_E, LCD_D4, LCD_D5, LCD_D6, LCD_D7, cols=LCD_COLS, rows=LCD_ROWS)
    face = FaceAnimator(lcd, col=FACE_COL, row_top=FACE_ROW_TOP)
    try:
        if mode == "look":
            face.animate_lookaround(dwell=220)
        elif mode == "talk":
            face.animate_talk(beats=10, tempo_ms=110)
        elif mode == "smile_only":
            face.mouth("smile")
            time.sleep_ms(600)
            face.mouth("neutral")
        elif mode in ("happy", "sad", "surprised", "wink", "sleepy", "annoyed", "grin"):
            face.expression(mode)
            time.sleep_ms(900)
        else:
            face.animate_blink(dt_open=1500, dt_close=120)
            face.animate_lookaround(dwell=220)
            face.animate_talk(beats=8, tempo_ms=110)
    finally:
        lcd.clear()
    print("[lcd] face animation done (mode={}, contrast={}%)".format(mode, contrast_percent))
    return {"mode": mode, "contrast_percent": contrast_percent}


def _run_parallel(commands):
    print("[sequence] running in parallel mode (limited support)")
    # Extract motor/buzzer tasks
    motor_cmd = None
    buzzer_cmd = None
    others = []
    
    for cmd in commands:
        name = cmd.get("name")
        if name == "activate_propeller":
            motor_cmd = cmd
        elif name == "buzzer_tone":
            buzzer_cmd = cmd
        else:
            others.append(cmd)
            
    results = []
    
    # Run simple tasks first (LEDs etc)
    for cmd in others:
        name = cmd.get("name")
        args = cmd.get("args", {})
        if name in FUNCTIONS:
            func = FUNCTIONS[name]["callable"]
            try:
                res = func(**args)
                results.append({"name": name, "ok": True, "result": res})
            except Exception as e:
                results.append({"name": name, "ok": False, "error": str(e)})
        else:
            results.append({"name": name, "ok": False, "error": "unknown function"})

    # Prepare duration
    durations = []
    
    # Start Motor
    if motor_cmd:
        args = motor_cmd.get("args", {})
        direction = args.get("direction", "forward")
        power = args.get("wind_power", _MOTOR_DEFAULT_SPEED)
        ms = args.get("duration_ms", 1500)
        durations.append(int(ms))
        
        direction_forward = (str(direction).lower() == "forward")
        SetMotorSpeed(direction_forward, int(power))
        results.append({"name": "activate_propeller", "ok": True, "status": "started"})
        
    # Start Buzzer
    buzzer = None
    if buzzer_cmd:
        args = buzzer_cmd.get("args", {})
        freq = int(args.get("freq_hz", 2000))
        ms = int(args.get("duration_ms", 300))
        duty = float(args.get("duty", 0.5))
        durations.append(ms)
        
        try:
            buzzer = PassiveBuzzer(BUZZER_PIN)
            duty_u16 = int(65535 * duty)
            buzzer.pwm.freq(freq)
            buzzer.pwm.duty_u16(duty_u16)
            results.append({"name": "buzzer_tone", "ok": True, "status": "started"})
        except Exception as e:
            results.append({"name": "buzzer_tone", "ok": False, "error": str(e)})
        
    # Wait
    max_ms = max(durations) if durations else 0
    if max_ms > 0:
        time.sleep_ms(max_ms)
        
    # Stop Motor
    if motor_cmd:
        brake = bool(motor_cmd.get("args", {}).get("brake", False))
        if brake:
            brake_motor()
        else:
            stop_motor()
            
    # Stop Buzzer
    if buzzer:
        buzzer.stop()
        
    return results


def run_sequence(commands: list = None, mode: str = "sequential"):
    """
    Run multiple commands. 
    commands: list of {"name": "func_name", "args": {..}}
    mode: 'sequential' or 'parallel'
    """
    if not commands:
        return []
        
    if mode == "parallel":
        return _run_parallel(commands)

    results = []
    for cmd in commands:
        name = cmd.get("name")
        args = cmd.get("args", {})
        
        if name not in FUNCTIONS:
            results.append({"name": name, "ok": False, "error": "unknown function"})
            continue
            
        spec = FUNCTIONS[name]
        func = spec["callable"]
        
        try:
            res = func(**args)
            results.append({"name": name, "ok": True, "result": res})
        except Exception as e:
            print("Error running {}: {}".format(name, e))
            results.append({"name": name, "ok": False, "error": str(e)})
            
    return results


# 関数ディスパッチテーブル
FUNCTIONS = {
    "run_sequence": {
        "callable": run_sequence,
        "description": "Run multiple commands in sequence or parallel.",
        "params": [
            {"name": "commands", "type": "list", "required": True},
            {"name": "mode", "type": "str", "default": "sequential", "required": False},
        ],
    },

    "led0_on": {
        "callable": led0_on,
        "description": "Turn on LED 0 (GPIO0, green, left).",
        "params": [],
    },
    "led0_off": {
        "callable": led0_off,
        "description": "Turn off LED 0 (GPIO0, green, left).",
        "params": [],
    },
    "led1_on": {
        "callable": led1_on,
        "description": "Turn on LED 1 (GPIO1, yellow/orange, right).",
        "params": [],
    },
    "led1_off": {
        "callable": led1_off,
        "description": "Turn off LED 1 (GPIO1, yellow/orange, right).",
        "params": [],
    },
    "temp": {
        "callable": read_temperature,
        "description": "Read internal temperature sensor (Celsius).",
        "params": [
            {"name": "samples", "type": "int", "default": 16, "required": False},
            {"name": "sample_interval_sec", "type": "float", "default": 0.01, "required": False},
        ],
    },
    "buzzer_tone": {
        "callable": buzzer_tone,
        "description": "Play a single tone on the passive buzzer (GPIO16).",
        "params": [
            {"name": "freq_hz", "type": "int", "default": 2000, "required": False},
            {"name": "duration_ms", "type": "int", "default": 300, "required": False},
            {"name": "duty", "type": "float", "default": 0.5, "required": False},
        ],
    },
    "buzzer_demo": {
        "callable": buzzer_demo,
        "description": "Play a short scale demo on the passive buzzer (GPIO16).",
        "params": [],
    },
    "buzzer_pattern": {
        "callable": buzzer_pattern,
        "description": "Play a preset buzzer pattern. Patterns: notify/alarm/confirm/fail/doorbell.",
        "params": [
            {"name": "pattern", "type": "str", "default": "notify", "required": False},
            {"name": "duty", "type": "float", "default": 0.4, "required": False},
        ],
    },
    "activate_propeller": {
        "callable": activate_propeller,
        "description": "Spin the propeller to generate wind via PWM enable (GPIO13) and IN1/IN2 (GPIO14/15).",
        "params": [
            {"name": "direction", "type": "str", "default": "forward", "required": False},
            {"name": "wind_power", "type": "int", "default": _MOTOR_DEFAULT_SPEED, "required": False},
            {"name": "duration_ms", "type": "int", "default": 1500, "required": False},
            {"name": "brake", "type": "bool", "default": False, "required": False},
        ],
    },
    "lcd_text": {
        "callable": lcd_text,
        "description": "Display up to two lines of ASCII text on the HD44780 LCD (GPIO2/3/6/7/8/9, contrast PWM GPIO12).",
        "params": [
            {"name": "line1", "type": "str", "default": "", "required": False},
            {"name": "line2", "type": "str", "default": "", "required": False},
            {"name": "contrast_percent", "type": "int", "default": CONTRAST_PERCENT_DEFAULT, "required": False},
            {"name": "duration_ms", "type": "int", "default": 0, "required": False},
        ],
    },
    "lcd_face": {
        "callable": lcd_face,
        "description": "Show a short face animation on the HD44780 LCD (GPIO2/3/6/7/8/9, contrast PWM GPIO12).",
        "params": [
            {"name": "mode", "type": "str", "default": "blink_cycle", "required": False,
             "enum": ["blink_cycle", "look", "talk", "smile_only", "happy", "sad", "surprised", "wink", "sleepy", "annoyed", "grin"]},
            {"name": "contrast_percent", "type": "int", "default": CONTRAST_PERCENT_DEFAULT, "required": False},
        ],
    },
}


def get_capabilities():
    """サーバーへ渡す capabilities 構造体を生成"""
    caps = []
    for name, spec in FUNCTIONS.items():
        caps.append({
            "name": name,
            "description": spec.get("description", ""),
            "params": spec.get("params", []),
        })
    return caps


def get_action_catalog():
    """ダッシュボード/LLM向けに提供する操作リストを生成"""
    actions = []
    for name, spec in FUNCTIONS.items():
        entry = {
            "name": name,
            "capability": name,
        }
        description = spec.get("description")
        if isinstance(description, str) and description:
            entry["description"] = description
        params = spec.get("params", [])
        if isinstance(params, list) and params:
            entry["params"] = params
        actions.append(entry)
    return actions


__all__ = [
    "FUNCTIONS",
    "CONTRAST_PERCENT_DEFAULT",
    "read_temperature",
    "led0_on",
    "led0_off",
    "led1_on",
    "led1_off",
    "buzzer_tone",
    "buzzer_demo",
    "buzzer_pattern",
    "activate_propeller",
    "lcd_text",
    "lcd_face",
    "get_capabilities",
    "get_action_catalog",
]

