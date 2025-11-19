import Jetson.GPIO as GPIO
import time

# ピン定義
TRIG_PIN = 5   # BCM5 (物理29)
ECHO_PIN = 6   # BCM6 (物理31)

GPIO.setmode(GPIO.BCM)
GPIO.setup(TRIG_PIN, GPIO.OUT)
GPIO.setup(ECHO_PIN, GPIO.IN)

def measure_distance():
    # センサーが安定するまで待機
    GPIO.output(TRIG_PIN, GPIO.LOW)
    time.sleep(0.1)  # 100ms待機

    # TRIGに超音波発射用のパルスを送出（10µs以上のHIGH）
    GPIO.output(TRIG_PIN, GPIO.HIGH)
    time.sleep(0.00001)  # 10マイクロ秒
    GPIO.output(TRIG_PIN, GPIO.LOW)

    # ECHOピンがHIGHになるのを待つ
    start_time = time.time()
    while GPIO.input(ECHO_PIN) == GPIO.LOW:
        start_time = time.time()
        # 一定時間待ってもHIGHにならなければタイムアウト処理を入れても良い

    # ECHOピンがLOWに戻るのを待つ
    stop_time = time.time()
    while GPIO.input(ECHO_PIN) == GPIO.HIGH:
        stop_time = time.time()

    # HIGHだった期間の計測
    elapsed = stop_time - start_time  # 単位: 秒

    # 音速 (34300 cm/s) を使って距離(cm)を計算
    distance = (elapsed * 34300) / 2  # 往復距離を半分に
    return distance

try:
    while True:
        dist = measure_distance()
        print(f"Distance: {dist:.2f} cm")
        time.sleep(1.0)
except KeyboardInterrupt:
    GPIO.cleanup()
