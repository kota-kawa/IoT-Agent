
import sys
import time
import random
import gc

# MicroPython/CPython 互換インポート
try:
    import network  # type: ignore
except Exception:
    network = None

try:
    import ure as re  # type: ignore
except Exception:
    import re

try:
    import ujson as json  # type: ignore
except Exception:
    import json

try:
    import usocket as socket  # type: ignore
except Exception:
    import socket

try:
    import ussl as ssl  # type: ignore
except Exception:
    import ssl

try:
    import uio as io  # type: ignore
except Exception:
    import io

try:
    import builtins  # print のラップに使用
except Exception:
    builtins = None  # ありえないが念のため

from machine import Pin, ADC, PWM, unique_id  # type: ignore

# =========================
# 設定
# =========================
# Flask サーバーへの接続先 URL と API パス
BASE_URL = "https://iot-agent.project-kk.com"
REGISTER_PATH = "/api/devices/register"
NEXT_PATH = "/api/devices/{device_id}/jobs/next"
RESULT_PATH = "/api/devices/{device_id}/jobs/result"

# Wi-Fi 認証情報は secrets.py から読み込み（無ければ未設定扱い）
WIFI_SSID = ""
WIFI_PASSWORD = ""
try:
    from secrets import WIFI_SSID as _SSID, WIFI_PASSWORD as _PW  # type: ignore
    WIFI_SSID = _SSID
    WIFI_PASSWORD = _PW
except Exception:
    pass

DEVICE_LABEL = ""
DEVICE_LOCATION = ""
try:
    from secrets import DEVICE_LABEL as _DEVICE_LABEL  # type: ignore
    DEVICE_LABEL = _DEVICE_LABEL
except Exception:
    pass
try:
    from secrets import DEVICE_LOCATION as _DEVICE_LOCATION  # type: ignore
    DEVICE_LOCATION = _DEVICE_LOCATION
except Exception:
    pass

# ポーリングや登録関連の挙動を制御するパラメータ
POLL_INTERVAL_SEC = 1  # 1秒間隔でサーバーをポーリング
AUTO_REGISTER_ON_BOOT = False  # True にすると起動時に自動登録
CAPABILITY_SYNC_ENABLED = True  # 手動登録後でも機能一覧をサーバーへ同期する
CAPABILITY_RESYNC_INTERVAL_SEC = 30  # 同期失敗時の再試行間隔（秒）

USER_AGENT = "MicroPython-IoT-Edge-Agent/1.1"
HTTP_BODY_PREVIEW_LEN = 512
HTTP_TIMEOUT_SEC = 15
_RECV_CHUNK = 1024
RESULT_MAX_ATTEMPTS = 4
RESULT_RETRY_BASE_DELAY = 2

def _format_for_log(value, max_length=400):
    """Convert arbitrary value to a short printable string."""
    # MicroPython 環境でも扱いやすいようログ出力文字列を整形
    try:
        text = json.dumps(value)
    except Exception:
        try:
            text = str(value)
        except Exception:
            text = "<unprintable>"

    if text and len(text) > max_length:
        return text[: max_length - 16] + "...<truncated>"
    return text

# =========================
# ハードウェア初期化
# =========================
LED_PIN = Pin("LED", Pin.OUT)
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
CONTRAST_PERCENT_DEFAULT = 25
PWM_FREQ_HZ = 100_000
FACE_COL = 4
FACE_ROW_TOP = 0

_wlan = None  # WLAN ハンドル
_NOT_REGISTERED_WARNED = False

# =========================
# ネットワーク/HTTP
# =========================
def ensure_wifi(max_wait_sec: int = 20) -> bool:
    """Wi-Fiへ接続済みでなければ接続する。成功時 True。"""
    global _wlan, WIFI_SSID, WIFI_PASSWORD, network
    if network is None:
        print("[net] network module not available.")
        return False

    if _wlan is not None and _wlan.isconnected():
        return True

    if not WIFI_SSID or not WIFI_PASSWORD:
        print("[net] WIFI_SSID/WIFI_PASSWORD not set (create secrets.py).")
        return False

    _wlan = network.WLAN(network.STA_IF)
    _wlan.active(True)
    if not _wlan.isconnected():
        print("[net] connecting SSID='{}' ...".format(WIFI_SSID))
        try:
            _wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        except Exception as e:
            print("[net] connect() error: {}".format(e))
            return False

        t0 = time.ticks_ms()
        while not _wlan.isconnected():
            if time.ticks_diff(time.ticks_ms(), t0) > max_wait_sec * 1000:
                print("\n[net] timeout.")
                return False
            time.sleep(0.5)
            print(".", end="")
        print("")

    if _wlan.isconnected():
        try:
            print("[net] connected: ip={}".format(_wlan.ifconfig()[0]))
        except Exception:
            print("[net] connected.")
        return True

    print("[net] failed to connect.")
    return False


def _parse_url(url: str):
    m = re.match(r"^https?://([^/]+)(/.*)?$", url)
    if not m:
        raise ValueError("Invalid URL")
    host = m.group(1)
    path = m.group(2) or "/"
    scheme = "https" if url.lower().startswith("https://") else "http"
    port = 443 if scheme == "https" else 80
    return scheme, host, port, path


def _http_request_raw(method: str, url: str, body: bytes = b"", headers: dict = None, timeout: int = HTTP_TIMEOUT_SEC):
    """urequests 非依存の最小HTTPクライアント。(status:int, bytes) を返す。"""
    headers = headers or {}
    scheme, host, port, path = _parse_url(url)

    addr_info = socket.getaddrinfo(host, port)[0][-1]
    s = socket.socket()
    try:
        try:
            s.settimeout(timeout)
        except Exception:
            pass
        s.connect(addr_info)
        if scheme == "https":
            try:
                s = ssl.wrap_socket(s, server_hostname=host)  # type: ignore
            except Exception:
                s = ssl.wrap_socket(s)  # type: ignore

        # Build request
        req_lines = [
            "{} {} HTTP/1.1".format(method, path),
            "Host: {}".format(host),
            "User-Agent: {}".format(USER_AGENT),
            "Accept: application/json",
            "Connection: close",
        ]
        if body:
            req_lines.append("Content-Length: {}".format(len(body)))
            # Content-Type は headers に委ねる
        for k, v in headers.items():
            req_lines.append("{}: {}".format(k, v))
        req = "\r\n".join(req_lines) + "\r\n\r\n"
        s.write(req.encode("utf-8"))
        if body:
            s.write(body)

        # Receive response
        chunks = []
        while True:
            buf = s.read(_RECV_CHUNK)
            if not buf:
                break
            chunks.append(buf)
        raw = b"".join(chunks)

    finally:
        try:
            s.close()
        except Exception:
            pass

    header, _, content = raw.partition(b"\r\n\r\n")
    # Status
    status = 0
    try:
        status_line = header.split(b"\r\n", 1)[0]
        status = int(status_line.split()[1])
    except Exception:
        status = 0
    return status, content


def http_get_text(url: str, timeout: int = HTTP_TIMEOUT_SEC):
    """GET -> (status:int, text:str)"""
    # Try urequests first
    try:
        import urequests as requests  # type: ignore
        r = requests.get(url, timeout=timeout)
        status = getattr(r, "status_code", 0)
        text = r.text
        try:
            r.close()
        except Exception:
            pass
        return int(status or 0), text
    except Exception:
        status, content = _http_request_raw("GET", url, b"", {}, timeout)
        try:
            text = content.decode("utf-8")
        except Exception:
            text = content.decode("latin-1", "ignore")
        return status, text


def http_post_json(url: str, obj, timeout: int = HTTP_TIMEOUT_SEC, extra_headers: dict = None):
    """POST JSON -> (status:int, text:str)"""
    payload = json.dumps(obj)
    headers = {"Content-Type": "application/json"}
    if extra_headers:
        for key, value in extra_headers.items():
            try:
                if value is None:
                    continue
                headers[str(key)] = str(value)
            except Exception:
                continue
    # Try urequests
    try:
        import urequests as requests  # type: ignore
        r = requests.post(url, data=payload, headers=headers, timeout=timeout)
        status = getattr(r, "status_code", 0)
        text = r.text
        try:
            r.close()
        except Exception:
            pass
        return int(status or 0), text
    except Exception:
        status, content = _http_request_raw("POST", url, payload.encode("utf-8"), headers, timeout)
        try:
            text = content.decode("utf-8")
        except Exception:
            text = content.decode("latin-1", "ignore")
        return status, text


def _load_device_id(path: str = "device_id.txt") -> str:
    """フラッシュから device_id を読み込み。無ければ作成して保存。"""
    try:
        with open(path, "r") as f:
            did = f.read().strip()
            if did:
                return did
    except Exception:
        pass

    # 新規作成: machine.unique_id() があればそれをHEX化
    try:
        raw = unique_id()  # type: ignore
        did = "".join("{:02x}".format(b) for b in raw)
    except Exception:
        rnd = random.getrandbits(64)
        did = "edge-" + "{:016x}".format(rnd)

    try:
        with open(path, "w") as f:
            f.write(did)
    except Exception:
        pass
    return did

# =========================
# デバイス提供関数
# =========================
def roll_dice():
    """サイコロ(1-6)"""
    v = random.randint(1, 6)
    print("[dice] roll -> {}".format(v))
    return v


def blink_led(times: int = 5, interval_sec: float = 0.2):
    """オンボードLED点滅"""
    if times < 1:
        raise ValueError("times must be >= 1")
    if interval_sec <= 0:
        raise ValueError("interval_sec must be > 0")
    print("[led] blinking {} times @ {:.3f}s".format(times, interval_sec))
    for _ in range(times):
        LED_PIN.value(1)
        time.sleep(interval_sec)
        LED_PIN.value(0)
        time.sleep(interval_sec)
    print("[led] done")
    return True


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


# =========================
# モーター（PWM + IN1/IN2）制御
# =========================
_motor_in1 = None
_motor_in2 = None
_motor_pwm = None
_MOTOR_INITIALISED = False
_PWM_RANGE_MAX = 4095
_U16_MAX = 65535
_MOTOR_DEFAULT_SPEED = 4095  # high enough to overcome startup friction
_MOTOR_MIN_START_SPEED = 3200  # below this many motors will not start reliably


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


def motor_drive(direction: str = "forward", speed: int = _MOTOR_DEFAULT_SPEED, duration_ms: int = 1500, brake: bool = False):
    """
    モーターを一定時間回す。direction: forward/reverse, speed:0..4095。
    duration_ms<=0 の場合は即座に停止（またはブレーキ）のみ行う。
    """
    direction = (direction or "").lower().strip()
    if direction not in ("forward", "reverse"):
        raise ValueError("direction must be 'forward' or 'reverse'")
    speed_int = int(speed)
    if speed_int < 0:
        speed_int = 0
    elif speed_int > _PWM_RANGE_MAX:
        speed_int = _PWM_RANGE_MAX
    # トルク不足で回らないケースを避けるため、0でないのに小さすぎる速度は底上げする
    if speed_int > 0 and speed_int < _MOTOR_MIN_START_SPEED:
        print("[motor] requested speed {} too low; raising to {}".format(speed, _MOTOR_MIN_START_SPEED))
        speed_int = _MOTOR_MIN_START_SPEED
    duration_ms_int = int(duration_ms)
    SetMotorSpeed(direction == "forward", speed_int)
    if duration_ms_int > 0:
        time.sleep_ms(duration_ms_int)
    if brake:
        brake_motor()
    else:
        stop_motor()
    print("[motor] dir={} speed={} duration={}ms brake={}".format(direction, speed_int, duration_ms_int, brake))
    return {
        "direction": direction,
        "speed": speed_int,
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
    max_percent = 40.0
    duty = int((percent * max_percent / 100.0) * 65535.0 / 100.0)
    _contrast_pwm.duty_u16(duty)


def _row(bits):
    if isinstance(bits, str):
        bits = bits.replace(".", "0").replace("#", "1")
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


def lcd_face(mode: str = "blink_cycle", contrast_percent: int = CONTRAST_PERCENT_DEFAULT):
    """
    LCDへ顔アニメーションを1サイクル表示。mode: blink_cycle/look/talk/smile_only。
    """
    set_contrast_percent(contrast_percent)
    lcd = HD44780(LCD_RS, LCD_E, LCD_D4, LCD_D5, LCD_D6, LCD_D7, cols=16, rows=2)
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
        else:
            face.animate_blink(dt_open=1500, dt_close=120)
            face.animate_lookaround(dwell=220)
            face.animate_talk(beats=8, tempo_ms=110)
    finally:
        lcd.clear()
    print("[lcd] face animation done (mode={}, contrast={}%)".format(mode, contrast_percent))
    return {"mode": mode, "contrast_percent": contrast_percent}


# 関数ディスパッチテーブル
FUNCTIONS = {
    "dice": {
        "callable": roll_dice,
        "description": "Roll a 6-sided dice and return result.",
        "params": [],  # no args
    },
    "led": {
        "callable": blink_led,
        "description": "Blink onboard LED.",
        "params": [
            {"name": "times", "type": "int", "default": 5, "required": False},
            {"name": "interval_sec", "type": "float", "default": 0.2, "required": False},
        ],
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
    "motor_drive": {
        "callable": motor_drive,
        "description": "Drive the motor via PWM enable (GPIO13) and IN1/IN2 (GPIO14/15).",
        "params": [
            {"name": "direction", "type": "str", "default": "forward", "required": False},
            {"name": "speed", "type": "int", "default": _MOTOR_DEFAULT_SPEED, "required": False},
            {"name": "duration_ms", "type": "int", "default": 1500, "required": False},
            {"name": "brake", "type": "bool", "default": False, "required": False},
        ],
    },
    "lcd_face": {
        "callable": lcd_face,
        "description": "Show a short face animation on the HD44780 LCD (GPIO2/3/6/7/8/9, contrast PWM GPIO12).",
        "params": [
            {"name": "mode", "type": "str", "default": "blink_cycle", "required": False},
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

# =========================
# LLMエージェント連携
# =========================
def register_device(base_url: str, device_id: str):
    url = base_url + REGISTER_PATH
    payload = {
        "device_id": device_id,
        "capabilities": get_capabilities(),
        "meta": {
            "firmware": "iot_edge_agent/1.1.0",
            "ua": USER_AGENT,
            "action_catalog": get_action_catalog(),
        },
    }
    if DEVICE_LABEL:
        payload["meta"]["label"] = DEVICE_LABEL
    if DEVICE_LOCATION:
        payload["meta"]["location"] = DEVICE_LOCATION
    print("[agent] register -> {}".format(url))
    status, text = http_post_json(url, payload, timeout=HTTP_TIMEOUT_SEC)
    print("[agent] register status {}".format(status))
    if text:
        preview = text if len(text) <= HTTP_BODY_PREVIEW_LEN else text[:HTTP_BODY_PREVIEW_LEN] + "\n...[truncated]"
        print("[agent] register resp preview:\n" + preview)
    return status


def fetch_next_job(base_url: str, device_id: str):
    global _NOT_REGISTERED_WARNED
    url = "{}{}".format(base_url, NEXT_PATH.format(device_id=device_id))
    status, text = http_get_text(url, timeout=HTTP_TIMEOUT_SEC)
    if status == 204 or (status == 200 and not text.strip()):
        if _NOT_REGISTERED_WARNED:
            _NOT_REGISTERED_WARNED = False
        return None  # no job
    if status != 200:
        if status == 404:
            if not _NOT_REGISTERED_WARNED:
                print(
                    "[agent] device not registered on server. Open the dashboard and use "
                    "the 'デバイス登録' button (https://iot-agent.project-kk.com/) while keeping "
                    "this script running."
                )
                _NOT_REGISTERED_WARNED = True
        else:
            if _NOT_REGISTERED_WARNED:
                _NOT_REGISTERED_WARNED = False
            print("[agent] next status {}".format(status))
        if text:
            preview = text if len(text) <= HTTP_BODY_PREVIEW_LEN else text[:HTTP_BODY_PREVIEW_LEN] + "\n...[truncated]"
            print("[agent] next resp preview:\n" + preview)
        return None
    if _NOT_REGISTERED_WARNED:
        _NOT_REGISTERED_WARNED = False
    try:
        job = json.loads(text)
        return job
    except Exception as e:
        print("[agent] JSON parse error: {}".format(e))
        return None


def post_result(
    base_url: str,
    device_id: str,
    job_id: str,
    ok: bool,
    return_value,
    stdout_text: str,
    stderr_text: str,
    *,
    max_attempts: int = RESULT_MAX_ATTEMPTS,
    backoff_base: int = RESULT_RETRY_BASE_DELAY,
) -> bool:
    # サーバーはパスパラメーターで device_id を受け取るため URL に埋め込む。
    # ボディとヘッダーにも同じ値を含めて送信し、整合性チェックに備える。
    url = "{}{}".format(base_url, RESULT_PATH.format(device_id=device_id))
    payload = {
        "device_id": device_id,
        "job_id": job_id,
        "ok": bool(ok),
        "return_value": return_value,
        "stdout": stdout_text or "",
        "stderr": stderr_text or "",
        "ts": time.ticks_ms() & 0x7fffffff,
    }
    extra_headers = {"X-Device-ID": device_id}
    attempt = 0
    while attempt < max_attempts:
        attempt += 1
        status, text = http_post_json(
            url,
            payload,
            timeout=HTTP_TIMEOUT_SEC,
            extra_headers=extra_headers,
        )
        print("[agent] result status {} (attempt {} of {})".format(status, attempt, max_attempts))
        if text:
            preview = text if len(text) <= HTTP_BODY_PREVIEW_LEN else text[:HTTP_BODY_PREVIEW_LEN] + "\n...[truncated]"
            print("[agent] result resp preview:\n" + preview)

        if 200 <= (status or 0) < 300:
            return True

        if attempt < max_attempts:
            delay = backoff_base * (2 ** (attempt - 1))
            if delay > 30:
                delay = 30
            print("[agent] result post failed (status {}). Retrying in {}s.".format(status, delay))
            time.sleep(delay)

    return False


def _call_function_by_name(name: str, args: dict):
    """指定名の関数をディスパッチして実行。戻り値を返す。"""
    if name not in FUNCTIONS:
        raise ValueError("unknown function: {}".format(name))
    spec = FUNCTIONS[name]
    func = spec["callable"]

    # 引数を用意（仕様上のdefaultを埋める）
    call_kwargs = {}
    for p in spec.get("params", []):
        pname = p["name"]
        if args is not None and pname in args:
            call_kwargs[pname] = args[pname]
        elif "default" in p:
            call_kwargs[pname] = p["default"]
        elif p.get("required", False):
            raise ValueError("missing required param: {}".format(pname))
    return func(**call_kwargs) if call_kwargs else func()


def _exec_with_capture(func, kwargs):
    """
    builtins.print を一時的にラップして stdout を捕捉。
    例外は sys.print_exception() で stderr バッファへ。
    """
    # 準備
    out_buf = io.StringIO()
    err_buf = io.StringIO()

    orig_print = builtins.print if builtins else print  # フォールバック

    def tee_print(*args, **kws):
        # sep/end/file を解釈
        sep = kws.pop("sep", " ")
        end = kws.pop("end", "\n")
        file = kws.pop("file", None)
        s = sep.join([str(x) for x in args]) + end
        try:
            out_buf.write(s)
        except Exception:
            pass
        # 元の print も呼ぶ
        try:
            if file is None:
                orig_print(*args, sep=sep, end=end)
            else:
                try:
                    orig_print(*args, sep=sep, end=end, file=file)
                except TypeError:
                    orig_print(*args, sep=sep, end=end)
        except Exception:
            # ここでの失敗は無視（とにかく進める）
            pass

    # 差し替え
    if builtins:
        builtins.print = tee_print

    ok = True
    ret = None
    try:
        ret = func(**(kwargs or {}))
    except Exception as e:
        ok = False
        # 詳細なスタックを err_buf へ
        try:
            if hasattr(sys, "print_exception"):
                sys.print_exception(e, err_buf)  # MicroPython 推奨
            else:
                # 最低限の文言
                err_buf.write("Exception: {}\n".format(e))
        except Exception:
            pass
    finally:
        # 復元
        if builtins:
            builtins.print = orig_print

    return ok, ret, out_buf.getvalue(), err_buf.getvalue()


def agent_loop():
    """Wi-Fi接続 -> 登録 -> 1秒ポーリング -> 実行 -> 結果返送"""
    if not ensure_wifi():
        print("[agent] Wi-Fi not connected; abort.")
        return

    device_id = _load_device_id()
    print("[agent] device_id={}".format(device_id))

    def _current_seconds():
        try:
            return float(time.time())
        except Exception:
            try:
                return float(time.ticks_ms()) / 1000.0
            except Exception:
                return 0.0

    capability_synced = False
    next_capability_sync = 0.0

    def _schedule_capability_sync(delay_sec: float):
        nonlocal next_capability_sync
        if delay_sec <= 0:
            next_capability_sync = 0.0
            return
        try:
            next_capability_sync = _current_seconds() + float(delay_sec)
        except Exception:
            next_capability_sync = float(delay_sec)

    def _attempt_capability_sync(reason: str) -> bool:
        nonlocal capability_synced
        if not CAPABILITY_SYNC_ENABLED:
            return True
        print("[agent] syncing capabilities ({}).".format(reason))
        try:
            status = register_device(BASE_URL, device_id)
        except Exception as exc:
            print("[agent] capability sync error ({}): {}".format(reason, exc))
            return False

        if 200 <= (status or 0) < 300:
            capability_synced = True
            print("[agent] capability sync succeeded (status {}).".format(status))
            return True

        if status == 403:
            print(
                "[agent] capability sync rejected (status 403). Register/approve this device "
                "from the dashboard first."
            )
        else:
            print("[agent] capability sync returned status {}.".format(status))
        return False

    if AUTO_REGISTER_ON_BOOT:
        if not _attempt_capability_sync("auto-register"):
            _schedule_capability_sync(CAPABILITY_RESYNC_INTERVAL_SEC)
    else:
        print(
            "[agent] auto registration is disabled. Register this device from the dashboard "
            "(https://iot-agent.project-kk.com/) before sending jobs."
        )
        if not _attempt_capability_sync("capability-sync"):
            _schedule_capability_sync(CAPABILITY_RESYNC_INTERVAL_SEC)

    backoff = 0
    pending_result = None
    pending_attempt = 0
    while True:
        try:
            if CAPABILITY_SYNC_ENABLED and not capability_synced:
                now_sec = _current_seconds()
                if next_capability_sync <= 0 or now_sec >= next_capability_sync:
                    if _attempt_capability_sync("scheduled"):
                        next_capability_sync = 0.0
                    else:
                        _schedule_capability_sync(CAPABILITY_RESYNC_INTERVAL_SEC)

            if pending_result is not None:
                job_id, ok, ret, out, err = pending_result
                print(
                    "[agent] retrying result delivery for job {} (attempt {}).".format(
                        job_id,
                        pending_attempt + 1,
                    )
                )
                success = post_result(
                    BASE_URL,
                    device_id,
                    job_id,
                    ok,
                    ret,
                    out,
                    err,
                    max_attempts=1,
                )
                if success:
                    print("[agent] result delivery confirmed for job {}".format(job_id))
                    print(
                        "[agent] job {} final return payload: {}".format(
                            job_id,
                            _format_for_log(ret),
                        )
                    )
                    pending_result = None
                    pending_attempt = 0
                    gc.collect()
                    time.sleep(POLL_INTERVAL_SEC)
                    continue
                else:
                    pending_attempt += 1
                    delay = RESULT_RETRY_BASE_DELAY * (2 ** (pending_attempt - 1))
                    if delay > 30:
                        delay = 30
                    print(
                        "[agent] result delivery still failing for job {}. Retrying in {}s.".format(
                            job_id, delay
                        )
                    )
                    time.sleep(delay)
                    continue

            job = fetch_next_job(BASE_URL, device_id)
            if not job:
                if backoff > 0:
                    backoff -= 1
                time.sleep(POLL_INTERVAL_SEC)
                continue

            raw_job_id = job.get("job_id") or job.get("id")
            job_id = str(raw_job_id) if raw_job_id is not None else ""
            cmd = job.get("command") or {}
            name = (cmd.get("name") or "").strip().lower()
            args = cmd.get("args") or {}

            print("[agent] job received: id={} name={} args={}".format(
                job_id,
                name,
                _format_for_log(args),
            ))

            if cmd.get("message"):
                print("[agent] job note: {}".format(_format_for_log(cmd.get("message"))))

            ok, ret, out, err = _exec_with_capture(
                _call_function_by_name, {"name": name, "args": args}
            )

            # 長文は切り詰め
            if out and len(out) > HTTP_BODY_PREVIEW_LEN:
                out = out[:HTTP_BODY_PREVIEW_LEN] + "\n...[truncated]"
            if err and len(err) > HTTP_BODY_PREVIEW_LEN:
                err = err[:HTTP_BODY_PREVIEW_LEN] + "\n...[truncated]"

            print(
                "[agent] exec finished for job {}: ok={} return={}".format(
                    job_id,
                    ok,
                    _format_for_log(ret),
                )
            )
            if out:
                print("[agent] job {} captured stdout:\n{}".format(job_id, out))
            if err:
                print("[agent] job {} captured stderr:\n{}".format(job_id, err))
            print(
                "[agent] job {} result summary -> ok={} return={} stdout_len={} stderr_len={}".format(
                    job_id,
                    ok,
                    _format_for_log(ret),
                    len(out or ""),
                    len(err or ""),
                )
            )
            backoff = 0
            pending_result = (job_id, ok, ret, out, err)
            pending_attempt = 0
            continue

        except KeyboardInterrupt:
            print("\n[agent] interrupted by user.")
            break
        except Exception as e:
            print("[agent] loop error: {}".format(e))
            # 軽いバックオフ
            sleep_s = POLL_INTERVAL_SEC + min(5, backoff)
            backoff = min(5, backoff + 1)
            time.sleep(sleep_s)

# エントリポイント：エージェント連携を起動
if __name__ == "__main__":
    agent_loop()

