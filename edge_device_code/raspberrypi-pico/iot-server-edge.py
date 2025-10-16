# -*- coding: utf-8 -*-
"""
Raspberry Pi Pico W (MicroPython) - LLMエージェント連携クライアント（デバイス側のみ）

変更点（2025-10-10）:
  * MicroPython の一部ビルドに sys.stdout / sys.stderr が無いため、
    標準出力の捕捉を「sys へ代入」→「builtins.print の一時ラップ（tee）」に変更。
  * 例外は sys.print_exception() で専用バッファへ書き出し。
  * それ以外の動作は従来通り：1秒ポーリングでジョブ取得→ローカル関数実行→結果POST。
  * ダッシュボードの UI 名称を「デバイス登録」に合わせて案内を更新。

機能:
  - Wi-Fi接続（secrets.py から SSID/PASS 読み込み）
  - デバイスID生成/保存（フラッシュに device_id.txt）
  - capabilities 登録（提供関数の一覧をサーバーへ通知）
  - 1秒間隔ポーリングでサーバーからジョブ(JSON)取得
  - 指示JSONに基づきローカル関数を実行し、結果をPOST返却

備考:
  - 2025-10-10 時点ではダッシュボードの「デバイス登録」から手動登録する運用を想定。
    自動登録を再有効化する場合は AUTO_REGISTER_ON_BOOT=True を設定する。

想定サーバーAPI:
  - POST {BASE_URL}{REGISTER_PATH}
      req: {"device_id": "...", "capabilities": [...], "meta": {...}}
      res: 200/201 JSON
  - GET  {BASE_URL}{NEXT_PATH}?device_id=...
      res: 204 (no job)
           200 {"job_id":"...", "command":{"name":"led","args":{"times":3,"interval_sec":0.1}}}
  - POST {BASE_URL}{RESULT_PATH}
      req: {"device_id":"...","job_id":"...","ok":true/false,
            "return_value":..., "stdout":"...", "stderr":"...","ts":123456789}
"""

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

from machine import Pin, ADC, unique_id  # type: ignore

# =========================
# 設定
# =========================
BASE_URL = "https://iot-agent.project-kk.com"
REGISTER_PATH = "/pico-w/register"
NEXT_PATH = "/pico-w/next"
RESULT_PATH = "/pico-w/result"

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

POLL_INTERVAL_SEC = 1  # 1秒間隔でサーバーをポーリング
AUTO_REGISTER_ON_BOOT = False  # True にすると起動時に自動登録

USER_AGENT = "PicoW-MicroPython-Agent/1.1"
HTTP_BODY_PREVIEW_LEN = 512
HTTP_TIMEOUT_SEC = 15
_RECV_CHUNK = 1024
RESULT_MAX_ATTEMPTS = 4
RESULT_RETRY_BASE_DELAY = 2

# =========================
# ハードウェア初期化
# =========================
LED_PIN = Pin("LED", Pin.OUT)
TEMP_ADC = ADC(4)
ADC_TO_VOLT = 3.3 / 65535.0

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
        did = "pico-" + "{:016x}".format(rnd)

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

# =========================
# LLMエージェント連携
# =========================
def register_device(base_url: str, device_id: str):
    url = base_url + REGISTER_PATH
    payload = {
        "device_id": device_id,
        "capabilities": get_capabilities(),
        "meta": {
            "firmware": "pico_w_agent/1.1.0",
            "ua": USER_AGENT,
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
    url = "{}{}?device_id={}".format(base_url, NEXT_PATH, device_id)
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
    # サーバー側で device_id をクエリパラメーターとして参照するケースがあり、
    # ボディのみでは 400 ("device_id is required") が返る状況が確認された。
    # 念のためクエリにも同じ値を付与して送信し、互換性を高める。
    url = "{}{}?device_id={}".format(base_url, RESULT_PATH, device_id)
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

    if AUTO_REGISTER_ON_BOOT:
        try:
            register_device(BASE_URL, device_id)
        except Exception as e:
            print("[agent] register error: {}".format(e))
    else:
        print(
            "[agent] auto registration is disabled. Register this device from the dashboard "
            "(https://iot-agent.project-kk.com/) before sending jobs."
        )

    backoff = 0
    pending_result = None
    pending_attempt = 0
    while True:
        try:
            if pending_result is not None:
                job_id, ok, ret, out, err = pending_result
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

            print("[agent] job received: id={} name={} args={}".format(job_id, name, args))

            ok, ret, out, err = _exec_with_capture(
                _call_function_by_name, {"name": name, "args": args}
            )

            # 長文は切り詰め
            if out and len(out) > HTTP_BODY_PREVIEW_LEN:
                out = out[:HTTP_BODY_PREVIEW_LEN] + "\n...[truncated]"
            if err and len(err) > HTTP_BODY_PREVIEW_LEN:
                err = err[:HTTP_BODY_PREVIEW_LEN] + "\n...[truncated]"

            print("[agent] exec ok={} ret={}".format(ok, ret))
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
