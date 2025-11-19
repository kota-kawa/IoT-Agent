#!/usr/bin/env python3
# ~/iot-agent/test/servo_test.py
# -*- coding: utf-8 -*-

"""
Raspberry Pi 4 + サーボ（3線：GND/5V/Signal）制御スクリプト（gpiozero版・2サーボ版）

- サーボは 3 本線（GND / +5V / 信号）を想定しています。
- 2 個のサーボを「別々の動き」で同時に動かします。
  - サーボ1: 0° ↔ 180° をゆっくりフルスイープ
  - サーボ2: 180° ↔ 0° をサーボ1と逆方向にスイープ（常に反対側を向く）

- 既存使用 GPIO（BCM）: 17, 22, 23, 24, 25, 27, 20, 21, 18, 26, 6, 13 は使用しません。
- 本スクリプトではサーボ信号線として BCM12 と BCM19 を使用します（どちらも未使用GPIO）。

【配線（色とGPIO/物理ピン）】
  ⚫ GND（黒/茶）:
      - Raspberry Pi の GND ピン
        （物理ピン 6 / 9 / 14 / 20 / 25 / 30 / 34 / 39 のいずれか）
      - 2つのサーボとも、この GND を共通に接続してください。

  🔴 +5V（赤）:
      - サーボ用の +5V 電源
      - 小型サーボ 1〜2 個であれば Raspberry Pi の 5V（物理ピン 2 または 4）
        から給電して動くこともありますが、電流不足で Pi がリセットする
        可能性があるため、「外部 5V 電源」を使うことを強く推奨します。
      - 外部 5V 電源を使う場合は、その GND を Raspberry Pi の GND と
        共通にしてください（共通 GND）。

  🟠 信号線（橙 / 黄 / 白）:
      - サーボ1（CH1）: Raspberry Pi の BCM12（物理ピン 32）
      - サーボ2（CH2）: Raspberry Pi の BCM19（物理ピン 35）

【動作イメージ（デフォルトの自動デモ）】
  1. 両方のサーボを 90°（中央）に合わせて 0.5 秒待機
  2. 以下を指定回数（デフォルト 3 回）繰り返す:
     - 0° → 180° まで angle を増やしながら
         サーボ1 = angle
         サーボ2 = 180 - angle   （互いに逆方向）
     - 180° → 0° まで angle を減らしながら
         サーボ1 = angle
         サーボ2 = 180 - angle
  3. 終了時に PWM を止めて（デタッチ）サーボへの力を抜く

【安全注意】
  - 外部 5V 電源を使う場合は Pi と共通 GND を必ず取ること。
  - +5V と GND の間に 470〜1000µF 程度の電解コンデンサを
    サーボ近くに入れると、電圧変動によるリセットを減らせます。
  - サーボにより可動範囲が異なるので、0〜180°フルスイープが
    機械的に無理そうな場合は、コード内の角度レンジを狭くしてください。
"""

from __future__ import annotations

import sys
import time
import argparse
from typing import Optional

from gpiozero import AngularServo, Device

# pigpio（高精度 PWM）のオプションサポート
try:
    from gpiozero.pins.pigpio import PiGPIOFactory
    HAVE_PIGPIO = True
except ImportError:
    HAVE_PIGPIO = False


# 他の回路で使用中の GPIO（BCM）はここに列挙して「使わない」ようにする
USED_BCM = {17, 22, 23, 24, 25, 27, 20, 21, 18, 26, 6, 13}

# このスクリプトでサーボに使用する GPIO（BCM）
SERVO1_PIN = 12  # 物理ピン 32: サーボ1 の信号線
SERVO2_PIN = 19  # 物理ピン 35: サーボ2 の信号線

if SERVO1_PIN in USED_BCM or SERVO2_PIN in USED_BCM:
    raise RuntimeError(
        f"サーボ用 GPIO が USED_BCM と衝突しています。"
        f"SERVO1_PIN={SERVO1_PIN}, SERVO2_PIN={SERVO2_PIN}, USED_BCM={sorted(USED_BCM)}"
    )

# サーボのパルス幅と角度レンジ（必要に応じて調整）
MIN_PULSE_WIDTH = 0.0005  # 0.5 ms
MAX_PULSE_WIDTH = 0.0025  # 2.5 ms
MIN_ANGLE = 0.0
MAX_ANGLE = 180.0


def create_servo(bcm_pin: int, use_pigpio: bool = False) -> AngularServo:
    """
    指定した GPIO（BCM 番号）に対応する AngularServo インスタンスを生成する。
    """
    if bcm_pin in USED_BCM:
        raise ValueError(
            f"GPIO{bcm_pin} は USED_BCM に登録されているため、サーボには使用できません。"
        )

    if use_pigpio:
        if not HAVE_PIGPIO:
            raise RuntimeError(
                "pigpio ピンファクトリが利用できません。"
                "  sudo apt install pigpio python3-gpiozero\n"
                "  sudo systemctl enable --now pigpiod\n"
                "  などで pigpiod を有効にしてください。"
            )
        # pigpio を pin_factory として使用
        Device.pin_factory = PiGPIOFactory()

    servo = AngularServo(
        bcm_pin,
        min_pulse_width=MIN_PULSE_WIDTH,
        max_pulse_width=MAX_PULSE_WIDTH,
        min_angle=MIN_ANGLE,
        max_angle=MAX_ANGLE,
    )
    return servo


def dual_demo(
    cycles: int = 3,
    step: float = 3.0,
    delay: float = 0.02,
    use_pigpio: bool = False,
) -> None:
    """
    2つのサーボを「違う動き」でスイープさせるデモ。
    - サーボ1: 0°→180°→0° のフルスイープ
    - サーボ2: サーボ1 と常に逆向き（angle2 = 180 - angle1）
    """
    servo1 = create_servo(SERVO1_PIN, use_pigpio=use_pigpio)
    servo2 = create_servo(SERVO2_PIN, use_pigpio=use_pigpio)

    try:
        print("=== 2サーボ デモ開始 ===")
        print(f"  サーボ1: GPIO{SERVO1_PIN}（物理ピン32）")
        print(f"  サーボ2: GPIO{SERVO2_PIN}（物理ピン35）")
        print(f"  cycles={cycles}, step={step}, delay={delay}s")
        print()

        # まず両方をセンター(90°)へ
        servo1.angle = 90.0
        servo2.angle = 90.0
        print("[DEMO] センタ位置 (90°) に移動して 0.5 秒待機...")
        time.sleep(0.5)

        # スイープループ
        for c in range(1, cycles + 1 if cycles > 0 else 10**9):
            print(f"[DEMO] cycle {c} / {cycles if cycles > 0 else '∞'}")

            # 0 -> 180
            angle = 0.0
            while angle <= 180.0:
                servo1.angle = angle
                servo2.angle = 180.0 - angle  # 逆向きに動かす
                print(f"  up:  servo1={angle:6.1f}°, servo2={180.0 - angle:6.1f}°")
                time.sleep(delay)
                angle += step

            # 180 -> 0
            angle = 180.0
            while angle >= 0.0:
                servo1.angle = angle
                servo2.angle = 180.0 - angle  # 逆向きに動かす
                print(f"  down:servo1={angle:6.1f}°, servo2={180.0 - angle:6.1f}°")
                time.sleep(delay)
                angle -= step

            if cycles > 0 and c >= cycles:
                break

        # 終了時は PWM を止めておく（サーボのトルクを抜く）
        servo1.value = None
        servo2.value = None
        print("[DEMO] 終了: PWM 停止（サーボデタッチ）")
    except KeyboardInterrupt:
        print("\n[DEMO] キーボード割り込みにより中断されました。")
        servo1.value = None
        servo2.value = None
    finally:
        servo1.close()
        servo2.close()
        print("[DEMO] サーボオブジェクトをクローズしました。")


def cmd_set(args: argparse.Namespace) -> None:
    """
    2つのサーボに任意の角度を設定するコマンド。
    """
    servo1 = create_servo(SERVO1_PIN, use_pigpio=args.pigpio)
    servo2 = create_servo(SERVO2_PIN, use_pigpio=args.pigpio)
    try:
        angle1 = float(args.angle1)
        angle2 = float(args.angle2)

        for name, angle in (("angle1", angle1), ("angle2", angle2)):
            if not (MIN_ANGLE <= angle <= MAX_ANGLE):
                raise ValueError(f"{name} は {MIN_ANGLE}〜{MAX_ANGLE} の範囲で指定してください。")

        servo1.angle = angle1
        servo2.angle = angle2
        print(f"[SET] サーボ1(GPIO{SERVO1_PIN}) -> {angle1:.1f}°")
        print(f"[SET] サーボ2(GPIO{SERVO2_PIN}) -> {angle2:.1f}°")

        if args.hold > 0:
            print(f"[SET] {args.hold} 秒間そのまま保持します。Ctrl+C で中断可。")
            time.sleep(args.hold)
    except KeyboardInterrupt:
        print("\n[SET] キーボード割り込みにより中断されました。")
    finally:
        # 任意: 終了時にトルクを抜きたい場合はコメントアウト解除
        # servo1.value = None
        # servo2.value = None
        servo1.close()
        servo2.close()
        print("[SET] サーボオブジェクトをクローズしました。")


def cmd_off(args: argparse.Namespace) -> None:
    """
    両方のサーボの PWM を停止してトルクを抜く。
    """
    servo1 = create_servo(SERVO1_PIN, use_pigpio=args.pigpio)
    servo2 = create_servo(SERVO2_PIN, use_pigpio=args.pigpio)
    try:
        servo1.value = None
        servo2.value = None
        print(f"[OFF] サーボ1(GPIO{SERVO1_PIN}) / サーボ2(GPIO{SERVO2_PIN}) の PWM を停止しました。")
        if args.hold > 0:
            time.sleep(args.hold)
    finally:
        servo1.close()
        servo2.close()
        print("[OFF] サーボオブジェクトをクローズしました。")


def cmd_demo(args: argparse.Namespace) -> None:
    """
    dual_demo を呼び出すラッパ。
    """
    dual_demo(
        cycles=args.cycles,
        step=args.step,
        delay=args.delay,
        use_pigpio=args.pigpio,
    )


def cmd_info(_: argparse.Namespace) -> None:
    """
    配線情報を表示するだけのコマンド。
    """
    print("=== 配線情報 (2サーボ) ===")
    print(" サーボ1 (CH1):")
    print("   信号線 -> BCM12 (物理ピン32)")
    print(" サーボ2 (CH2):")
    print("   信号線 -> BCM19 (物理ピン35)")
    print(" 共通:")
    print("   GND  -> Raspberry Pi の GND ピン (6/9/14/20/25/30/34/39)")
    print("   +5V  -> サーボ用の 5V 電源（Pi の 2/4 または外部 5V）")
    print(" ※ USED_BCM（他用途で使用中）:", sorted(USED_BCM))


def build_parser() -> argparse.ArgumentParser:
    """
    サブコマンド付き引数パーサを構築する。
    - デフォルト（サブコマンド省略）の場合は demo を実行。
    """
    parser = argparse.ArgumentParser(
        description="Raspberry Pi 4 用 2サーボ制御スクリプト（gpiozero / AngularServo）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    sub = parser.add_subparsers(dest="command", required=False)

    # 共通オプション
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--pigpio",
        action="store_true",
        help="pigpio ピンファクトリを使用（pigpiod が起動している必要があります）",
    )
    common.add_argument(
        "--hold",
        type=float,
        default=0.0,
        help="処理後にこの秒数だけ待機して終了（0 の場合は待機なし）",
    )

    # demo
    p_demo = sub.add_parser(
        "demo",
        parents=[common],
        help="2つのサーボを逆向きにスイープさせるデモを実行",
    )
    p_demo.add_argument(
        "--cycles",
        type=int,
        default=3,
        help="往復回数（0 を指定すると無限ループ）",
    )
    p_demo.add_argument(
        "--step",
        type=float,
        default=3.0,
        help="角度の刻み（度）",
    )
    p_demo.add_argument(
        "--delay",
        type=float,
        default=0.02,
        help="ステップ間の待ち時間（秒）",
    )
    p_demo.set_defaults(func=cmd_demo)

    # set
    p_set = sub.add_parser(
        "set",
        parents=[common],
        help="2つのサーボに任意の角度を設定する",
    )
    p_set.add_argument(
        "--angle1",
        type=float,
        required=True,
        help="サーボ1 (GPIO12) に設定する角度（度）",
    )
    p_set.add_argument(
        "--angle2",
        type=float,
        required=True,
        help="サーボ2 (GPIO19) に設定する角度（度）",
    )
    p_set.set_defaults(func=cmd_set)

    # off
    p_off = sub.add_parser(
        "off",
        parents=[common],
        help="両方のサーボの PWM を停止する（トルクを抜く）",
    )
    p_off.set_defaults(func=cmd_off)

    # info
    p_info = sub.add_parser(
        "info",
        help="配線情報を表示するだけ",
    )
    p_info.set_defaults(func=cmd_info)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    parser = build_parser()
    args = parser.parse_args(argv)

    # サブコマンド省略時は demo を実行
    if args.command is None:
        demo_args = parser.parse_args(["demo"])
        cmd_demo(demo_args)
        return 0

    # サブコマンド指定時
    try:
        args.func(args)  # type: ignore[attr-defined]
        return 0
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())