# ファイル: main.py
# Raspberry Pi Pico W (MicroPython) 用
# 元のArduinoスケッチの挙動をPythonで再現
#
# 配線:
#   PIN_ENABLE (GPIO13) -> モータドライバのENABLE(PWM入力)
#   PIN_IN_1   (GPIO14) -> モータドライバのIN1
#   PIN_IN_2   (GPIO15) -> モータドライバのIN2
#
# 注意:
#  - Picoから直にモータは駆動せず、必ずモータドライバ(L298N, TB6612FNG, DRV8833等)経由で駆動してください。
#  - 供給電圧/電流はドライバとモータの仕様に合わせてください。

from machine import Pin, PWM
import time

# === GPIO設定（Arduinoスケッチ準拠） ===
PIN_ENABLE = 13
PIN_IN_1   = 14
PIN_IN_2   = 15

# === 初期化 ===
in1 = Pin(PIN_IN_1, Pin.OUT)
in2 = Pin(PIN_IN_2, Pin.OUT)

pwm_enable = PWM(Pin(PIN_ENABLE))
# Arduinoの analogWriteFreq(25000) 相当
pwm_enable.freq(25_000)

# 初期は停止
pwm_enable.duty_u16(0)
in1.value(0)
in2.value(0)

# Arduinoの analogWriteRange(4096) 相当（0〜4095）
_PWM_RANGE_MAX = 4095
_U16_MAX = 65535

def _scale_12bit_to_u16(speed_0_4095: int) -> int:
    """
    0..4095 の値を MicroPython の duty_u16 (0..65535) に線形変換する。
    上限・下限は本関数呼び出し前にクリップする設計だが、念のため安全側で再クリップ。
    """
    if speed_0_4095 < 0:
        speed_0_4095 = 0
    elif speed_0_4095 > _PWM_RANGE_MAX:
        speed_0_4095 = _PWM_RANGE_MAX
    # 4095 -> 65535 となるようスケーリング（丸め誤差を抑えるために+0.5で四捨五入）
    duty = int((speed_0_4095 * _U16_MAX) / _PWM_RANGE_MAX + 0.5)
    if duty < 0:
        duty = 0
    elif duty > _U16_MAX:
        duty = _U16_MAX
    return duty

def SetMotorSpeed(dir: bool, speed: int) -> None:
    """
    回転方向と速度を設定する。Arduino版に合わせて speed は 0..4095 想定。
    dir=True  で正転 (IN1=HIGH, IN2=LOW)
    dir=False で逆転 (IN1=LOW,  IN2=HIGH)
    speed は <0 なら停止、>4095 なら最大とみなす
    """
    # --- 回転方向の設定 ---
    if dir:
        in1.value(1)
        in2.value(0)
    else:
        in1.value(0)
        in2.value(1)

    # --- 回転速度の設定（12bit→16bitへ変換してPWM出力）---
    if speed < 0:
        duty = 0
    elif speed > _PWM_RANGE_MAX:
        duty = _U16_MAX
    else:
        duty = _scale_12bit_to_u16(speed)

    pwm_enable.duty_u16(duty)

def stop_motor() -> None:
    """安全に停止（PWM=0、ブレーキはせず惰性で停止）"""
    pwm_enable.duty_u16(0)

def brake_motor() -> None:
    """
    簡易ブレーキ: 両INを同一レベルにしてモータ端子を短絡ブレーキ（ドライバにより挙動は異なる）。
    必要に応じて stop_motor と使い分けてください。
    """
    in1.value(1)
    in2.value(1)
    pwm_enable.duty_u16(0)

def main() -> None:
    # Arduinoの setup() 内のシリアル出力に相当
    # MicroPython では print がUSBシリアル(REPL)へ出ます
    print("初期化完了: 25kHz PWM / 12bitスケール (0..4095)")

    try:
        while True:
            print("正転・低速")
            SetMotorSpeed(True, 3500)
            time.sleep_ms(3000)

            print("正転・高速")
            SetMotorSpeed(True, 4095)
            time.sleep_ms(3000)

            print("逆転・低速")
            SetMotorSpeed(False, 3500)
            time.sleep_ms(3000)

            print("逆転・高速")
            SetMotorSpeed(False, 4095)
            time.sleep_ms(3000)

    except KeyboardInterrupt:
        # Ctrl+C で安全停止
        print("割り込み検出: モータ停止中…")
        stop_motor()
        # 必要ならブレーキ:
        # brake_motor()
        # PWMを解放したい場合はコメント解除:
        # pwm_enable.deinit()
        in1.value(0)
        in2.value(0)
        print("停止完了")

if __name__ == "__main__":
    main()
