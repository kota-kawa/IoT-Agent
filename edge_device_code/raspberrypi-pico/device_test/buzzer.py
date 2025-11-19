from machine import Pin, PWM
import time

# ===== 設定（使用GPIOは1本のみ：GPIO16） =====
BUZZER_PIN = 16  # パッシブブザー用（物理ピン21）

class PassiveBuzzer:
    """
    パッシブブザー（圧電スピーカ）をPWMで鳴らす簡易クラス。
    - tone(freq_hz, duration_ms, duty): 指定周波数・時間で鳴らす
    - silence(duration_ms): 休符
    - play(sequence): シーケンス再生 [(freq, ms, duty省略可), 休符ms, ...]
    追加:
    - stop(): 終了時に必ず無音化（duty=0 → PWM停止 → ピンLOW固定）
    - コンテキストマネージャ対応（with ... as ...）
    """
    def __init__(self, pin=BUZZER_PIN):
        self.pin_no = pin
        self.pin = Pin(pin, Pin.OUT)
        self.pwm = PWM(self.pin)
        # 初期状態は無音
        self.pwm.duty_u16(0)
        self._stopped = False

    def tone(self, freq_hz: int, duration_ms: int, duty: float = 0.5):
        """
        freq_hz: 発振周波数（Hz）
        duration_ms: 鳴らす時間（ミリ秒）
        duty: デューティ（0.0〜1.0）。0.4〜0.6くらいが音量と歪みのバランス良好。
        """
        if freq_hz <= 0:
            self.silence(duration_ms)
            return

        # 周波数設定（※同一PWMスライスを共有するピンが無ければこのピンだけに効く）
        self.pwm.freq(int(freq_hz))

        # デューティ設定（16bit）
        duty = 0.0 if duty is None else max(0.0, min(1.0, float(duty)))
        duty_u16 = int(65535 * duty)
        # デューティが0だと完全無音なので、極小音でも鳴らしたい場合は最小1にする
        if duty_u16 == 0 and freq_hz > 0:
            duty_u16 = 1

        self.pwm.duty_u16(duty_u16)
        time.sleep_ms(int(duration_ms))
        # 鳴らし終わったら無音へ
        self.pwm.duty_u16(0)

    def silence(self, duration_ms: int):
        self.pwm.duty_u16(0)
        time.sleep_ms(int(duration_ms))

    def play(self, sequence):
        """
        sequence: [(freq, ms[, duty]), 休符ms, ...] のリスト
        例: [(440,200), 50, (494,200,0.4), ...]
        """
        for item in sequence:
            if isinstance(item, (list, tuple)):
                if len(item) == 2:
                    f, ms = item
                    d = 0.5
                else:
                    f, ms, d = item[0], item[1], item[2]
                self.tone(f, ms, d)
            else:
                # 数値だけ渡されたら休符として扱う
                self.silence(int(item))

    def stop(self):
        """
        必ず無音で終了させるための後始末。
        1) duty=0（PWM出力を無音に）
        2) PWMを停止（deinit）
        3) ピンを通常GPIO出力LOWに固定（浮きや残留発振の予防）
        """
        if self._stopped:
            return
        try:
            self.pwm.duty_u16(0)
        except Exception:
            pass
        try:
            self.pwm.deinit()
        except Exception:
            pass
        try:
            # PWM機能から通常GPIOに戻してLOW固定
            self.pin = Pin(self.pin_no, Pin.OUT)
            self.pin.value(0)
        except Exception:
            pass
        self._stopped = True

    def deinit(self):
        # 既存コード互換
        self.stop()

    # ---- with 文対応 ----
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.stop()
        # 例外は呼び出し側へ伝播させる（False）
        return False

def demo():
    """
    簡単なデモ：ドレミファソラシド → 休符 → 逆順
    パッシブブザーは 2kHz 前後が鳴らしやすいですが、音階例として 260〜520Hz 帯を使用。
    """
    # with を使うことで、正常終了/例外/KeyboardInterrupt でも必ず無音化
    with PassiveBuzzer(BUZZER_PIN) as buz:
        # 周波数（おおよそ）
        C4=261; D4=294; E4=329; F4=349; G4=392; A4=440; B4=494; C5=523
        quarter = 200  # 200ms

        seq = [
            (C4, quarter), (D4, quarter), (E4, quarter), (F4, quarter),
            (G4, quarter), (A4, quarter), (B4, quarter), (C5, quarter),
            200,  # 休符
            (C5, 200), (B4, 200), (A4, 200), (G4, 200),
            (F4, 200), (E4, 200), (D4, 200), (C4, 400),
        ]
        buz.play(seq)

        # 単発ビープ例（2kHzを300ms）
        time.sleep_ms(300)
        buz.tone(2000, 300, duty=0.5)

if __name__ == "__main__":
    try:
        demo()
    except KeyboardInterrupt:
        # Ctrl+C でも with の __exit__ が走るため無音化される
        pass

