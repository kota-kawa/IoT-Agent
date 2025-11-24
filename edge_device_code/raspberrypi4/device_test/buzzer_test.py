#!/usr/bin/env python3

from gpiozero import TonalBuzzer
from time import sleep

# パッシブブザーを接続したGPIOピン番号（例: GPIO18）
BUZZER_PIN = 4

def main():
    buzzer = TonalBuzzer(BUZZER_PIN)

    try:
        # 「ラ(A4)」を1秒鳴らす
        buzzer.play('A4')
        sleep(1.0)

        # 「ド(C5)」を1秒鳴らす
        buzzer.play('C5')
        sleep(1.0)

        # 停止
        buzzer.stop()

    finally:
        # 何かあっても必ず止める
        buzzer.stop()

if __name__ == "__main__":
    main()
