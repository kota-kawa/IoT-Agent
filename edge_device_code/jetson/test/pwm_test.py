# /home/kota/jetson-agent/examples/pwm33_blink_sysfs_fixed.py
# Jetson Orin Nano / JetPack 6.x
# 物理33番(BCM13) = PWM5 = pwm@32c0000 = /sys/class/pwm/pwmchip2/pwm0
# をsysfs経由でハードPWMし、フェード点滅させるスクリプト。
# ※ root 権限で実行してください（sudo -E python3 ...）

import os
import time
import sys

PWMCHIP = "/sys/class/pwm/pwmchip2"  # ← あなたの環境の出力より 32c0000 は pwmchip2
CHAN    = "pwm0"

def w(path, val):
    with open(path, "w") as f:
        f.write(str(val))

def r(path):
    with open(path, "r") as f:
        return f.read().strip()

def main():
    if os.geteuid() != 0:
        print("root 権限で実行してください（sudo -E python3 ...）", file=sys.stderr)
        sys.exit(1)

    # export
    if not os.path.isdir(os.path.join(PWMCHIP, CHAN)):
        try:
            w(os.path.join(PWMCHIP, "export"), 0)
        except Exception as e:
            # 既にexport済みなら無視
            pass

    pwm_dir = os.path.join(PWMCHIP, CHAN)
    if not os.path.isdir(pwm_dir):
        print(f"ERROR: {pwm_dir} が見つかりません。pwm5(33番)がDTで割り当てられていない可能性があります。", file=sys.stderr)
        sys.exit(2)

    enable = os.path.join(pwm_dir, "enable")
    period = os.path.join(pwm_dir, "period")
    duty   = os.path.join(pwm_dir, "duty_cycle")

    # 安全のため一旦停止
    try:
        w(enable, 0)
    except Exception:
        pass

    # 周期を設定（1kHz=1,000,000ns。ただし最小period制約のため、失敗時は20msに切替）
    set_ok = False
    for T in (1_000_000, 20_000_000):
        try:
            w(period, T)
            set_ok = True
            break
        except Exception:
            continue
    if not set_ok:
        print("ERROR: period の設定に失敗しました。", file=sys.stderr)
        sys.exit(3)

    # 50% dutyで開始
    P = int(r(period))
    w(duty, P // 2)
    w(enable, 1)

    print("物理33番(BCM13/PWM5) フェード開始 (Ctrl+Cで終了)")
    try:
        while True:
            # フェードイン
            for dc in range(0, 101, 2):
                w(duty, P * dc // 100)
                time.sleep(0.01)
            # フェードアウト
            for dc in range(100, -1, -2):
                w(duty, P * dc // 100)
                time.sleep(0.01)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            w(enable, 0)
        except Exception:
            pass
        print("停止しました。")

if __name__ == "__main__":
    main()

