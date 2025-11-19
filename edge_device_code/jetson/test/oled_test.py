#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Jetson Orin Nano + SH1107 OLED 簡易テスト (I2C エラー耐性版)

- I2C: /dev/i2c-7 （J12 ピン 3/5）
- I2C アドレス: 0x3C
- 低 FPS / 小さな描画で I2C 負荷を軽く
- フレーム送信中の OSError(121) / DeviceNotFoundError を検出し、
  ・何回かはフレームを捨てるだけ
  ・連続エラー時だけ再初期化を試行
- 再初期化が失敗してもプログラムは落とさず、一定時間ごとに再チャレンジ
- Ctrl+C で終了（終了時に画面クリア、失敗しても無視）
"""

import sys
import time
import traceback
from typing import Optional

from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import sh1107
from luma.core.error import DeviceNotFoundError

# ==== 設定 ====

# Jetson Orin Nano J12 の I2C1 は /dev/i2c-7 (ピン 3: SDA, 5: SCL)
# ※もしピン 27/28 側の I2C に配線を変えた場合は 1 などに変更
I2C_BUS = 7
I2C_ADDR = 0x3C

WIDTH = 128
HEIGHT = 128

# 負荷をかなり落として 2 FPS
FPS = 2.0
FRAME_INTERVAL = 1.0 / FPS

# 初期化リトライ回数
MAX_INIT_RETRY = 3

# 連続エラー許容回数（これを超えたら再初期化を試す）
MAX_ERROR_STREAK_BEFORE_RESET = 5

# 再初期化時のリトライ回数
MAX_RECOVER_RETRY = 3


# ==== ログユーティリティ ====

def log_info(msg: str) -> None:
    print(f"[INFO] {msg}", flush=True)


def log_warn(msg: str) -> None:
    print(f"[WARN] {msg}", flush=True)


def log_error(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr, flush=True)


# ==== I2C / デバイス初期化 ====

def create_serial() -> i2c:
    """
    luma.core.interface.serial.i2c の __init__ シグネチャを確認して、
    bus_speed_hz がサポートされている環境でのみ指定する。
    （サポートされていない環境でキーワードを渡すと TypeError になる）
    """
    kwargs = {
        "port": I2C_BUS,
        "address": I2C_ADDR,
    }

    # 実際の i2c.__init__ の引数名を確認
    try:
        varnames = i2c.__init__.__code__.co_varnames
    except Exception:
        varnames = ()

    if "bus_speed_hz" in varnames:
        # Jetson のカーネル設定とは別に、ドライバが対応している場合は 100kHz を指定
        kwargs["bus_speed_hz"] = 100_000
    else:
        log_warn("luma.i2c does not support 'bus_speed_hz' keyword. Using default I2C speed.")

    return i2c(**kwargs)


def init_device_once() -> sh1107:
    """
    単発の初期化。失敗したら例外をそのまま投げる。
    """
    log_info(f"Initializing OLED on /dev/i2c-{I2C_BUS} addr=0x{I2C_ADDR:02X}")
    serial = create_serial()

    dev = sh1107(serial, width=WIDTH, height=HEIGHT)

    # 終了時に自動で display OFF されて消えるのが嫌なら cleanup を上書き
    def _noop_cleanup(self) -> None:  # type: ignore[override]
        # 何もしない
        return

    # luma.core.device.device.cleanup(self) と同じシグネチャになるようにバインド
    dev.cleanup = _noop_cleanup.__get__(dev, dev.__class__)  # type: ignore[assignment]

    return dev


def init_device_with_retry() -> sh1107:
    """
    初期起動時用のリトライ付き初期化。
    OSError / DeviceNotFoundError の場合に数回リトライして最後の例外を投げる。
    """
    last_exc: Optional[BaseException] = None

    for attempt in range(1, MAX_INIT_RETRY + 1):
        try:
            log_info(
                f"Initializing OLED on /dev/i2c-{I2C_BUS} addr=0x{I2C_ADDR:02X} "
                f"(attempt {attempt}/{MAX_INIT_RETRY})"
            )
            dev = init_device_once()
            log_info("OLED init success.")
            return dev
        except (OSError, DeviceNotFoundError) as e:
            last_exc = e
            log_warn(f"OLED init failed: {e}")
            time.sleep(0.3)
        except Exception as e:
            last_exc = e
            log_error(f"OLED init unexpected error: {e}")
            traceback.print_exc()
            time.sleep(0.3)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("OLED init failed for unknown reason")


def try_recover_device(device: Optional[sh1107], frame_no: int) -> Optional[sh1107]:
    """
    フレーム送信中に I2C エラーが多発した場合に呼び出される再初期化処理。
    ここで例外を投げず、最終的に None を返すことで上位ループは「デバイス喪失」として扱う。
    """
    last_exc: Optional[BaseException] = None

    for attempt in range(1, MAX_RECOVER_RETRY + 1):
        try:
            log_info(
                f"Recovering I2C/OLED (frame={frame_no}, attempt {attempt}/{MAX_RECOVER_RETRY})"
            )

            # 既存デバイスがあれば一応 cleanup（上書きしているので実質 no-op）
            if device is not None:
                try:
                    device.cleanup()
                except Exception:
                    pass

            dev = init_device_once()
            log_info("Recovered I2C/OLED successfully.")
            return dev
        except (OSError, DeviceNotFoundError) as e:
            last_exc = e
            log_warn(f"Recover attempt {attempt}/{MAX_RECOVER_RETRY} failed: {e}")
            time.sleep(0.3)
        except Exception as e:
            last_exc = e
            log_error(f"Recover unexpected error: {e}")
            traceback.print_exc()
            time.sleep(0.3)

    if last_exc is not None:
        log_error(f"Failed to recover OLED after {MAX_RECOVER_RETRY} attempts: {last_exc}")
    else:
        log_error(f"Failed to recover OLED after {MAX_RECOVER_RETRY} attempts: unknown error")

    return None


# ==== 描画 ====

def draw_frame(device: sh1107, frame_no: int) -> None:
    """
    I2C 負荷を極力減らした、シンプルなアニメーション。
    - 画面全体の枠
    - 画面中央を左右に往復するバー
    - 左上にフレーム番号
    """
    # 往復運動するバーの位置計算
    bar_len = 20
    step = 4
    max_x = WIDTH - bar_len - 1

    raw_pos = (frame_no * step) % (2 * max_x)
    if raw_pos <= max_x:
        x = raw_pos
    else:
        x = 2 * max_x - raw_pos

    with canvas(device) as draw:
        # 画面クリア兼枠
        draw.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), outline=255, fill=0)
        # 中央バー
        y = HEIGHT // 2
        draw.rectangle((x, y - 2, x + bar_len, y + 2), outline=255, fill=255)
        # フレーム番号
        draw.text((2, 2), f"{frame_no:03d}", fill=255)


# ==== メインループ ====

def main() -> None:
    print(
        f"[INFO] Running simple animation on /dev/i2c-{I2C_BUS} "
        f"addr=0x{I2C_ADDR:02X} {WIDTH}x{HEIGHT}",
        flush=True,
    )

    device: Optional[sh1107] = None
    error_streak = 0
    frame_no = 0

    try:
        # 起動時の初回初期化
        device = init_device_with_retry()
        next_frame_time = time.monotonic()

        while True:
            now = time.monotonic()
            if now < next_frame_time:
                time.sleep(next_frame_time - now)
            next_frame_time += FRAME_INTERVAL

            frame_no += 1

            # デバイスが喪失しているときはフレーム前に再初期化を試す
            if device is None:
                try:
                    device = init_device_with_retry()
                    error_streak = 0
                except Exception as e:
                    log_warn(f"Init during loop failed: {e}")
                    time.sleep(0.5)
                    continue

            try:
                # 通常の描画
                draw_frame(device, frame_no)
                error_streak = 0

            except (OSError, DeviceNotFoundError) as e:
                log_warn(f"I2C error during frame {frame_no}: {e}. (error_streak={error_streak + 1})")
                error_streak += 1

                # まだ連続エラー回数が閾値未満なら「このフレームを捨てるだけ」
                if error_streak < MAX_ERROR_STREAK_BEFORE_RESET:
                    continue

                # 閾値を超えたら再初期化を試す
                error_streak = 0
                device = try_recover_device(device, frame_no)

                # 再初期化に失敗して device が None の場合は、少し待って次のループへ
                if device is None:
                    time.sleep(0.5)

                continue

            except Exception as e:
                # 予期せぬ例外はログに出しつつループ継続
                log_error(f"Unexpected error during frame {frame_no}: {e}")
                traceback.print_exc()
                time.sleep(0.5)
                continue

    except KeyboardInterrupt:
        pass
    finally:
        # 終了時は画面クリア（失敗しても無視）
        try:
            if device is not None:
                try:
                    device.clear()
                except Exception:
                    pass
        finally:
            print("\n[INFO] Stopped and cleared display.", flush=True)


if __name__ == "__main__":
    main()
