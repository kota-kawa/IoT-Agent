#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from picamera2 import Picamera2
from datetime import datetime
from pathlib import Path
import time

# ---- 保存先ディレクトリ（ハードコード） ----
SAVE_DIR = Path("/home/kota/iot-agent/test")  # ここを任意の固定パスに変更可
SAVE_DIR.mkdir(parents=True, exist_ok=True)

# ---- ファイル名（タイムスタンプ） ----
outfile = SAVE_DIR / f"rpi_{datetime.now():%Y%m%d_%H%M%S}.jpg"

# ---- 撮影（最小構成）----
picam2 = Picamera2()
picam2.configure(picam2.create_still_configuration())
picam2.start()
time.sleep(1.2)  # 露出/ホワイトバランス安定待ち（環境で調整可）
picam2.capture_file(str(outfile))
picam2.stop()

print(f"Saved: {outfile}")