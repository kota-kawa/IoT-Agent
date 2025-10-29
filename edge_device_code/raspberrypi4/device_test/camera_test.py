#!/usr/bin/env python3
from picamera2 import Picamera2
from datetime import datetime
from pathlib import Path
import time

picam2 = Picamera2()
picam2.configure(picam2.create_still_configuration())
picam2.start()
time.sleep(1.2)  # 露出などの安定待ち
out = Path.home() / "Pictures" / f"rpi_{datetime.now():%Y%m%d_%H%M%S}.jpg"
out.parent.mkdir(parents=True, exist_ok=True)
picam2.capture_file(str(out))
picam2.stop()
print(f"Saved: {out}")
