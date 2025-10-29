#!/usr/bin/env python3
# /home/pi/motor/diag_l293d_basic.py
# L293D: ENをHigh固定、IN1/IN2で正逆を確認（BCM: EN=25/17, IN=24/23 と 27/22）

from time import sleep
from gpiozero import OutputDevice

# BCM pins (Hackster記事の配線と同じ)
Motor1 = {"EN": 25, "IN1": 24, "IN2": 23}
Motor2 = {"EN": 17, "IN1": 27, "IN2": 22}

en1 = OutputDevice(Motor1["EN"], active_high=True, initial_value=True)  # 常時High
en2 = OutputDevice(Motor2["EN"], active_high=True, initial_value=True)  # 常時High

in1_1 = OutputDevice(Motor1["IN1"])
in1_2 = OutputDevice(Motor1["IN2"])
in2_1 = OutputDevice(Motor2["IN1"])
in2_2 = OutputDevice(Motor2["IN2"])

def forward():
    in1_1.on();  in1_2.off()
    in2_1.on();  in2_2.off()

def backward():
    in1_1.off(); in1_2.on()
    in2_1.off(); in2_2.on()

def coast():
    en1.off(); en2.off()     # 出力無効（惰性）※ブレーキではない
    sleep(2.0)
    en1.on();  en2.on()      # 再度有効化

try:
    print("FORWARD 5s")
    forward(); sleep(5)
    print("COAST 2s")
    coast()
    print("BACKWARD 5s")
    backward(); sleep(5)
    print("DONE")
finally:
    # 安全停止
    en1.off(); en2.off()
    in1_1.off(); in1_2.off(); in2_1.off(); in2_2.off()
