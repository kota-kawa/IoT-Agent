import Jetson.GPIO as GPIO
import time

PIR_PIN = 26  # BCM26 (物理37)

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(True)
GPIO.setup(PIR_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

print("PIR sensor monitoring (press Ctrl+C to exit)")
try:
    last = -1
    while True:
        val = GPIO.input(PIR_PIN)
        if val != last:
            print(f"Raw={val}")  # 生レベルを変化時に出す（デバッグ用）
            last = val

        if val == GPIO.HIGH:
            print("Motion Detected!")
            time.sleep(2.0)  # 検知後は少し待つ（多重出力を抑制）
        else:
            time.sleep(0.1)
except KeyboardInterrupt:
    pass
finally:
    GPIO.cleanup(PIR_PIN)
