#!/usr/bin/env python3
# ~/iot-agent/test/servo_test.py
# -*- coding: utf-8 -*-

"""
Raspberry Pi 4 + サーボ（3線：GND/5V/Signal）制御スクリプト（gpiozero版）
- 既存使用GPIO（BCM）：17, 22, 23, 24, 25, 27, 20, 21, 18, 26, 6, 13 は使用しません。
- 既定のサーボ信号ピンは BCM12（物理ピン32）を使用します（CH1）。
- 無引数で起動した場合：非対話の自動デモを実行（info表示 → センタ移動 → 小振りスイープ → PWM停止）。

【配線（色とGPIO/物理ピン）】
  ⚫ GND（黒/茶）   -> Raspberry Pi の GND（物理 6/9/14/20/25/30/34/39 のいずれか）
  🔴 +5V（赤）     -> 外部5V電源の +5V （推奨）※小型1個なら Pi の 5V（物理2/4）も動作例ありだがリスクあり
  🟠 信号（橙/黄/白）-> CH1: GPIO12（BCM12 / 物理ピン32）

【安全注意】
- 外部5V電源使用時は Pi の GND と外部電源の GND を共通にしてください（共通GND）。
- +5V と GND の間に 470〜1000µF 程度の電解コンデンサをサーボ近くに配置推奨。
- サーボ個体差により可動範囲やパルス幅が異なります。無理な可動はギア破損の恐れ。

【未使用の空きGPIO（例）】※あなたの使用済みリストは避けています
  CH1: BCM12(物理32), CH2: BCM19(物理35), CH3: BCM5(物理29), CH4: BCM4(物理7)

【無引数デモのシーケンス】
  1) info表示
  2) CH1を 90° へ移動して 0.5秒保持
  3) 60°↔120° を 2°刻みで 2往復（delay 0.02s）
  4) PWM停止（保持解除）
"""

from __future__ import annotations
import time
import sys
import argparse
from typing import Dict, Optional

from gpiozero import AngularServo, Device

# pigpio（任意・高安定）サポート
try:
    from gpiozero.pins.pigpio import PiGPIOFactory  # optional
    _HAS_PIGPIO = True
except Exception:
    _HAS_PIGPIO = False


# ------------------------------------------------------------
# 既使用GPIOの除外とデフォルトピン割り当て
# ------------------------------------------------------------
_USED_BCM = {17, 22, 23, 24, 25, 27, 20, 21, 18, 26, 6, 13}

_DEFAULT_CHANNEL_PINS: Dict[int, int] = {
    1: 12,  # 物理32
    2: 19,  # 物理35
    3: 5,   # 物理29
    4: 4,   # 物理7
}

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
        Device.pin_factory = PiGPIOFactory()  # localhost

    servo = AngularServo(
        bcm_pin,
        min_angle=min_angle,
        max_angle=max_angle,
        min_pulse_width=min_pulse_width,
        max_pulse_width=max_pulse_width,
        frame_width=0.02,  # 20ms（50Hz）
    )
    return servo


# ------------------------------------------------------------
# コマンド実装（サブコマンド用）
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
        servo.value = None  # PWM停止（デタッチ）
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
        cycles = int(args.cycles)
        count = 0
        print(f"[SWEEP] CH{args.channel}: GPIO{bcm} {start}度→{end}度（{step}度刻み, delay={delay}s）")
        while True:
            a = start
            while a <= end:
                servo.angle = a
                print(f" angle={a:.1f}")
                time.sleep(delay)
                a += step
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
    print(" ※ 使用済GPIO: 17, 22, 23, 24, 25, 27, 20, 21, 18, 26, 6, 13 は回避済み。")


# ------------------------------------------------------------
# 無引数時の非対話デモ
# ------------------------------------------------------------
def autorun_demo() -> int:
    """
    非対話の安全デモ：
      1) info表示
      2) CH1を90度へ → 0.5秒保持
      3) 60°↔120° を2往復（2°刻み, 0.02s/step）
      4) PWM停止
    """
    cmd_info(argparse.Namespace())

    # デモ設定
    channel = 1
    bcm = _DEFAULT_CHANNEL_PINS[channel]
    use_pigpio = False
    min_pw = 0.0005
    max_pw = 0.0025
    center_angle = 90.0
    sweep_start = 60.0
    sweep_end = 120.0
    sweep_step = 2.0
    sweep_delay = 0.02
    sweep_cycles = 2

    servo = create_servo(
        bcm_pin=bcm,
        use_pigpio=use_pigpio,
        min_pulse_width=min_pw,
        max_pulse_width=max_pw,
        min_angle=0.0,
        max_angle=180.0,
    )
    try:
        # センタへ
        servo.angle = center_angle
        print(f"[DEMO] CH{channel}: GPIO{bcm} -> {center_angle:.1f}度（センタ）保持0.5s")
        time.sleep(0.5)

        # 小振りスイープ
        print(f"[DEMO] スイープ開始: {sweep_start}°↔{sweep_end}°, step={sweep_step}°, delay={sweep_delay}s, cycles={sweep_cycles}")
        count = 0
        while True:
            # 60 -> 120
            a = sweep_start
            while a <= sweep_end:
                servo.angle = a
                print(f" angle={a:.1f}")
                time.sleep(sweep_delay)
                a += sweep_step
            # 120 -> 60
            a = sweep_end
            while a >= sweep_start:
                servo.angle = a
                print(f" angle={a:.1f}")
                time.sleep(sweep_delay)
                a -= sweep_step

            count += 1
            if count >= sweep_cycles:
                break

        # PWM停止
        servo.value = None
        print("[DEMO] PWM停止（保持解除）")
        return 0
    except KeyboardInterrupt:
        print("[DEMO] 中断されました。")
        return 1
    finally:
        servo.close()


# ------------------------------------------------------------
# 引数パーサ（サブコマンド省略可）
# ------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Raspberry Pi 4 サーボ制御（gpiozero/AngularServo）。角度は0〜180度で指定。無引数時は非対話デモを自動実行。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        add_help=True,
    )
    sub = p.add_subparsers(dest="cmd", required=False)  # 無引数対応

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


# ------------------------------------------------------------
# エントリポイント
# ------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.cmd is None:
            # ★ サブコマンド省略時：非対話デモを自動実行
            return autorun_demo()
        else:
            # サブコマンド実行
            args.func(args)
            return 0
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())