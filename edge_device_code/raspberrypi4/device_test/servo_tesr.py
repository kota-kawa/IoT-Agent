#!/usr/bin/env python3
# /home/kota/iot-agent/test/servo_test.py
# -*- coding: utf-8 -*-

"""
Raspberry Pi 4 + サーボ（3線：GND/5V/Signal）制御スクリプト（gpiozero版）
- サブコマンド無しでも動作する「ダイレクトモード」対応。
- 既存使用GPIO（BCM）：17, 22, 23, 24, 25, 27, 20, 21, 18, 26, 6, 13 は使用しません。
- 既定のサーボ信号ピンは BCM12（物理ピン32）を使用します。

【配線（色とGPIO/物理ピン）】
  ⚫ GND（黒/茶）     -> Raspberry Pi の GND（物理 6/9/14/20/25/30/34/39）
  🔴 +5V（赤）       -> 外部5V電源の +5V（推奨。小型1個のみなら Pi の 5V(物理2/4)でも動く例あり）
  🟠 信号（橙/黄/白） -> GPIO12（BCM12 / 物理ピン32）

【安全注意】
- 外部5V電源使用時は Pi の GND と外部電源の GND を共通にしてください（共通GND）。
- +5V と GND の間に 470〜1000µF 程度の電解コンデンサをサーボ近くに配置推奨。
- サーボ個体差により可動範囲やパルス幅が異なります。無理な可動はギア破損の恐れ。

【ダイレクトモード（サブコマンド不要の例）】
  # 配線情報だけ表示（安全デフォルト）
  python3 servo_test.py

  # 角度を直接指定
  python3 servo_test.py --angle 90

  # センタへ
  python3 servo_test.py --center

  # PWM停止（デタッチ）
  python3 servo_test.py --off

  # スイープ
  python3 servo_test.py --sweep --start 0 --end 180 --step 2 --delay 0.02 --cycles 2

【従来のサブコマンドも可】
  python3 servo_test.py info
  python3 servo_test.py set --angle 45
  python3 servo_test.py center
  python3 servo_test.py off
  python3 servo_test.py sweep --cycles 2
"""

from __future__ import annotations
import time
import sys
import argparse
from typing import Dict, Optional

from gpiozero import AngularServo
from gpiozero import Device
try:
    from gpiozero.pins.pigpio import PiGPIOFactory  # optional
    _HAS_PIGPIO = True
except Exception:
    _HAS_PIGPIO = False


# ------------------------------------------------------------
# 設定：未使用のGPIOだけをデフォルトにする
# ------------------------------------------------------------
# あなたの環境で「使用済み」と言及のあったGPIOを避ける
_USED_BCM = {17, 22, 23, 24, 25, 27, 20, 21, 18, 26, 6, 13}

# デフォルトで使う候補（未使用ピンのみ）
_DEFAULT_CHANNEL_PINS: Dict[int, int] = {
    1: 12,  # 物理32
    2: 19,  # 物理35
    3: 5,   # 物理29
    4: 4,   # 物理7
}

# 使用済みが混ざっていないか一応チェック
for ch, bcm in list(_DEFAULT_CHANNEL_PINS.items()):
    if bcm in _USED_BCM:
        raise RuntimeError(f"デフォルト割当のCH{ch} -> GPIO{bcm} が '使用済み' リストと衝突しています。別の空きGPIOに変更してください。")


# ------------------------------------------------------------
# サーボ生成ユーティリティ
# ------------------------------------------------------------
def create_servo(
    bcm_pin: int,
    use_pigpio: bool = False,
    min_pulse_width: float = 0.0005,   # 0.5ms（個体差で調整）
    max_pulse_width: float = 0.0025,   # 2.5ms（個体差で調整）
    min_angle: float = 0.0,
    max_angle: float = 180.0,
) -> AngularServo:
    """
    AngularServo を生成して返す。
    - pigpio を使うときは pigpiod が起動している必要あり（sudo systemctl start pigpiod 等）。
    - パルス幅はサーボに合わせて調整してください（1.0ms〜2.0ms が仕様の個体も多い）。
    """
    if bcm_pin in _USED_BCM:
        raise ValueError(f"指定されたGPIO{bcm_pin}は '使用済み' リストに含まれています。別のGPIOを指定してください。")

    if use_pigpio:
        if not _HAS_PIGPIO:
            raise RuntimeError("pigpio のピンファクトリが利用できません。'python3 -m pip install pigpio' および 'sudo apt-get install pigpio' 後、'sudo systemctl start pigpiod' を実行してください。")
        Device.pin_factory = PiGPIOFactory()  # localhost デフォルト

    servo = AngularServo(
        bcm_pin,
        min_angle=min_angle,
        max_angle=max_angle,
        min_pulse_width=min_pulse_width,
        max_pulse_width=max_pulse_width,
        frame_width=0.02,  # 20ms（50Hz相当）
    )
    return servo


# ------------------------------------------------------------
# コマンド実装
# ------------------------------------------------------------
def cmd_set(args: argparse.Namespace) -> None:
    bcm = _DEFAULT_CHANNEL_PINS.get(args.channel, None)
    if bcm is None:
        raise ValueError(f"不正なチャンネル番号: {args.channel}")

    servo = create_servo(
        bcm_pin=bcm,
        use_pigpio=args.pigpio,
        min_pulse_width=args.min_pw,
        max_pulse_width=args.max_pw,
        min_angle=0.0,
        max_angle=180.0,
    )
    try:
        angle = float(args.angle)
        if not (0.0 <= angle <= 180.0):
            raise ValueError("角度は0〜180の範囲で指定してください。")
        servo.angle = angle
        print(f"[SET] CH{args.channel}: GPIO{bcm} -> {angle:.1f}度")
        if args.hold > 0:
            time.sleep(args.hold)
    finally:
        servo.close()


def cmd_center(args: argparse.Namespace) -> None:
    bcm = _DEFAULT_CHANNEL_PINS.get(args.channel, None)
    if bcm is None:
        raise ValueError(f"不正なチャンネル番号: {args.channel}")

    servo = create_servo(
        bcm_pin=bcm,
        use_pigpio=args.pigpio,
        min_pulse_width=args.min_pw,
        max_pulse_width=args.max_pw,
        min_angle=0.0,
        max_angle=180.0,
    )
    try:
        servo.angle = 90.0
        print(f"[CENTER] CH{args.channel}: GPIO{bcm} -> 90.0度（センタ）")
        if args.hold > 0:
            time.sleep(args.hold)
    finally:
        servo.close()


def cmd_off(args: argparse.Namespace) -> None:
    bcm = _DEFAULT_CHANNEL_PINS.get(args.channel, None)
    if bcm is None:
        raise ValueError(f"不正なチャンネル番号: {args.channel}")

    servo = create_servo(
        bcm_pin=bcm,
        use_pigpio=args.pigpio,
        min_pulse_width=args.min_pw,
        max_pulse_width=args.max_pw,
        min_angle=0.0,
        max_angle=180.0,
    )
    try:
        # PWM信号を停止（デタッチ）
        servo.value = None
        print(f"[OFF] CH{args.channel}: GPIO{bcm} -> PWM停止（デタッチ）")
        if args.hold > 0:
            time.sleep(args.hold)
    finally:
        servo.close()


def cmd_sweep(args: argparse.Namespace) -> None:
    bcm = _DEFAULT_CHANNEL_PINS.get(args.channel, None)
    if bcm is None:
        raise ValueError(f"不正なチャンネル番号: {args.channel}")

    start = float(args.start)
    end = float(args.end)
    step = float(args.step)
    delay = float(args.delay)
    if step <= 0:
        raise ValueError("step は正の値にしてください。")
    if not (0.0 <= start <= 180.0 and 0.0 <= end <= 180.0):
        raise ValueError("start / end は 0〜180 の範囲で指定してください。")

    servo = create_servo(
        bcm_pin=bcm,
        use_pigpio=args.pigpio,
        min_pulse_width=args.min_pw,
        max_pulse_width=args.max_pw,
        min_angle=0.0,
        max_angle=180.0,
    )
    try:
        # 往復スイープを指定回数（0:無限）実行
        cycles = int(args.cycles)
        count = 0
        print(f"[SWEEP] CH{args.channel}: GPIO{bcm} {start}度→{end}度（{step}度刻み, delay={delay}s）")
        while True:
            # 昇順
            a = start
            while a <= end:
                servo.angle = a
                print(f" angle={a:.1f}")
                time.sleep(delay)
                a += step
            # 降順
            a = end
            while a >= start:
                servo.angle = a
                print(f" angle={a:.1f}")
                time.sleep(delay)
                a -= step

            if cycles > 0:
                count += 1
                if count >= cycles:
                    break
    except KeyboardInterrupt:
        pass
    finally:
        servo.close()


def cmd_info(_: argparse.Namespace) -> None:
    print("=== サーボ配線（色とGPIO/物理ピン） ===")
    print(" ⚫ GND（黒/茶） : Raspberry Pi の GND（物理 6/9/14/20/25/30/34/39）")
    print(" 🔴 +5V（赤）   : 外部5V推奨（Piの 2/4 でも小型1個なら動作例あり）")
    print(" 🟠 信号（橙/黄/白）: CH1->GPIO12(物理32), CH2->GPIO19(物理35), CH3->GPIO5(物理29), CH4->GPIO4(物理7)")
    print(" ※ 使用済GPIO: 17, 22, 23, 24, 25, 27, 20, 21, 18, 26, 6, 13 は避けています。")


# ------------------------------------------------------------
# 解析・エントリポイント
# ------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Raspberry Pi 4 サーボ制御（gpiozero/AngularServo）。サブコマンド無しでも動作。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        add_help=True,
    )

    # ---------- グローバル共通オプション（ダイレクトモードでも使用） ----------
    p.add_argument("--channel", type=int, default=1, help="サーボチャンネル（1〜4）")
    p.add_argument("--pigpio", action="store_true", help="pigpio ピンファクトリを使用（pigpiod が起動していること）")
    p.add_argument("--min-pw", dest="min_pw", type=float, default=0.0005, help="min_pulse_width（秒）例: 0.0005=0.5ms")
    p.add_argument("--max-pw", dest="max_pw", type=float, default=0.0025, help="max_pulse_width（秒）例: 0.0025=2.5ms")
    p.add_argument("--hold", type=float, default=0.0, help="コマンド実行後にこの秒数だけ保持して終了")

    # ---------- ダイレクトモード用のアクション指定（サブコマンド不要） ----------
    p.add_argument("--angle", type=float, help="角度を直接指定（0〜180）。指定時は set と同等。")
    p.add_argument("--center", action="store_true", help="センタ（90度）へ移動。")
    p.add_argument("--off", action="store_true", help="PWM 停止（デタッチ）。")
    p.add_argument("--sweep", action="store_true", help="スイープ動作を実行。")
    p.add_argument("--start", type=float, default=0.0, help="スイープ開始角度（--sweep時）")
    p.add_argument("--end", type=float, default=180.0, help="スイープ終了角度（--sweep時）")
    p.add_argument("--step", type=float, default=1.0, help="スイープ刻み角度（--sweep時）")
    p.add_argument("--delay", type=float, default=0.01, help="各ステップ間の待ち秒数（--sweep時）")
    p.add_argument("--cycles", type=int, default=1, help="スイープ往復回数（0で無限。--sweep時）")

    # ---------- 従来のサブコマンド（互換のため残す。必須にはしない） ----------
    sub = p.add_subparsers(dest="cmd", required=False)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--channel", type=int, default=1, help="サーボチャンネル（1〜4）")
    common.add_argument("--pigpio", action="store_true", help="pigpio ピンファクトリを使用（pigpiod が起動していること）")
    common.add_argument("--min-pw", dest="min_pw", type=float, default=0.0005, help="min_pulse_width（秒）例: 0.0005=0.5ms")
    common.add_argument("--max-pw", dest="max_pw", type=float, default=0.0025, help="max_pulse_width（秒）例: 0.0025=2.5ms")
    common.add_argument("--hold", type=float, default=0.0, help="コマンド実行後にこの秒数だけ保持して終了")

    sp_set = sub.add_parser("set", parents=[common], help="角度を設定（0〜180）")
    sp_set.add_argument("--angle", type=float, required=True, help="設定角度（0〜180）")
    sp_set.set_defaults(func=cmd_set)

    sp_center = sub.add_parser("center", parents=[common], help="90度（センタ）に移動")
    sp_center.set_defaults(func=cmd_center)

    sp_off = sub.add_parser("off", parents=[common], help="PWM停止（デタッチ）")
    sp_off.set_defaults(func=cmd_off)

    sp_sweep = sub.add_parser("sweep", parents=[common], help="角度スイープ（往復）")
    sp_sweep.add_argument("--start", type=float, default=0.0, help="開始角度")
    sp_sweep.add_argument("--end", type=float, default=180.0, help="終了角度")
    sp_sweep.add_argument("--step", type=float, default=1.0, help="刻み角度")
    sp_sweep.add_argument("--delay", type=float, default=0.01, help="各ステップ間の待ち秒数")
    sp_sweep.add_argument("--cycles", type=int, default=1, help="往復回数（0で無限）")
    sp_sweep.set_defaults(func=cmd_sweep)

    sp_info = sub.add_parser("info", help="配線とピン割り当てを表示")
    sp_info.set_defaults(func=cmd_info)

    return p


def _count_direct_actions(args: argparse.Namespace) -> int:
    cnt = 0
    if args.angle is not None:
        cnt += 1
    if args.center:
        cnt += 1
    if args.off:
        cnt += 1
    if args.sweep:
        cnt += 1
    return cnt


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    # サブコマンド必須ではない
    args = parser.parse_args(argv)

    try:
        # 1) サブコマンドが指定されていれば従来通り
        if args.cmd:
            return args.func(args) or 0

        # 2) ダイレクトモード（サブコマンド無しでの判定）
        act_cnt = _count_direct_actions(args)
        if act_cnt > 1:
            raise ValueError("同時に複数の動作は指定できません。--angle / --center / --off / --sweep のいずれか1つにしてください。")

        if args.angle is not None:
            # set 相当
            if not (0.0 <= args.angle <= 180.0):
                raise ValueError("角度は0〜180の範囲で指定してください。")
            cmd_set(args)
            return 0

        if args.center:
            cmd_center(args)
            return 0

        if args.off:
            cmd_off(args)
            return 0

        if args.sweep:
            cmd_sweep(args)
            return 0

        # 3) 何も指定が無ければ安全のため info を表示（自動駆動はしない）
        cmd_info(args)
        return 0

    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
