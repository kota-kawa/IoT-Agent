from edge_compat import builtins, gc, io, json, sys, time
from edge_actions import FUNCTIONS, get_action_catalog, get_capabilities
from edge_config import (
    AUTO_REGISTER_ON_BOOT,
    BASE_URL,
    CAPABILITY_RESYNC_INTERVAL_SEC,
    CAPABILITY_SYNC_ENABLED,
    DEVICE_LABEL,
    DEVICE_LOCATION,
    HTTP_BODY_PREVIEW_LEN,
    HTTP_TIMEOUT_SEC,
    NEXT_PATH,
    POLL_INTERVAL_SEC,
    REGISTER_PATH,
    RESULT_MAX_ATTEMPTS,
    RESULT_PATH,
    RESULT_RETRY_BASE_DELAY,
    USER_AGENT,
)
from edge_device_id import _load_device_id
from edge_network import ensure_wifi, http_get_text, http_post_json
from edge_utils import _format_for_log, _truncate_text

_NOT_REGISTERED_WARNED = False


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
        job = None
        job_id = ""
        name = ""
        args = {}
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

            ret = _truncate_text(ret)
            out = _truncate_text(out)
            err = _truncate_text(err)

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
            if job_id:
                try:
                    post_result(
                        BASE_URL,
                        device_id,
                        job_id,
                        False,
                        None,
                        "",
                        _truncate_text(str(e)),
                        max_attempts=1,
                    )
                except Exception as post_err:
                    print("[agent] failed to report error for job {}: {}".format(job_id, post_err))
            # 軽いバックオフ
            sleep_s = POLL_INTERVAL_SEC + min(5, backoff)
            backoff = min(5, backoff + 1)
            time.sleep(sleep_s)


__all__ = [
    "agent_loop",
    "fetch_next_job",
    "post_result",
    "register_device",
    "_call_function_by_name",
    "_exec_with_capture",
]
