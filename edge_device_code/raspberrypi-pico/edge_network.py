from edge_compat import network, re, json, socket, ssl, time
from edge_config import HTTP_TIMEOUT_SEC, USER_AGENT, WIFI_PASSWORD, WIFI_SSID, _RECV_CHUNK

_wlan = None  # WLAN ハンドル


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


def _http_request_raw(
    method: str,
    url: str,
    body: bytes = b"",
    headers: dict = None,
    timeout: int = HTTP_TIMEOUT_SEC,
):
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


__all__ = ["ensure_wifi", "http_get_text", "http_post_json"]

