import threading
import time
import logging
from typing import Dict, Any, List

from iot_agent.models import DeviceState
from iot_agent.storage import get_store

logger = logging.getLogger("iot-agent.virtual")

class VirtualDeviceRunner:
    def __init__(self, device_id: str = "virtual-device-01", display_name: str = "仮想デモデバイス"):
        self.device_id = device_id
        self.display_name = display_name
        self.running = False
        self._thread = None
        
        # Virtual Device State
        self.state = {
            "power": "off",
            "color": "white",
            "brightness": 100,
            "display_text": "Ready",
            "motor_status": "stopped"
        }

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(f"Virtual device {self.device_id} started.")

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join()

    def _ensure_registered(self):
        store = get_store()
        existing = store.get_device(self.device_id)
        
        # Capabilities matching what the LLM expects for lights/robots
        capabilities = [
            {"name": "turn_on", "description": "電源をオンにする", "parameters": {}},
            {"name": "turn_off", "description": "電源をオフにする", "parameters": {}},
            {"name": "set_color", "description": "LEDの色を変更する", "parameters": {"type": "object", "properties": {"color": {"type": "string"}}}},
            {"name": "set_display", "description": "ディスプレイにテキストを表示する", "parameters": {"type": "object", "properties": {"text": {"type": "string"}}}},
            {"name": "control_motor", "description": "モーターを制御する", "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["start", "stop"]}, "speed": {"type": "integer"}}}},
            {"name": "get_status", "description": "現在の状態を取得する", "parameters": {}},
        ]

        if not existing:
            device = DeviceState(
                device_id=self.device_id,
                capabilities=capabilities,
                meta={"display_name": self.display_name, "virtual": True},
                approved=True,
                last_seen=time.time()
            )
            store.save_device(device)
            logger.info(f"Registered virtual device {self.device_id}")
        else:
            # Update capabilities if needed
            existing.capabilities = capabilities
            if not existing.meta.get("display_name"):
                existing.meta["display_name"] = self.display_name
            existing.meta["virtual"] = True
            existing.approved = True
            store.save_device(existing)

    def _loop(self):
        # Initial registration
        try:
            self._ensure_registered()
        except Exception as e:
            logger.error(f"Failed to register virtual device: {e}")

        while self.running:
            try:
                self._heartbeat()
                self._process_jobs()
            except Exception as e:
                logger.error(f"Error in virtual device loop: {e}")
            
            time.sleep(1) # Poll every 1 second for responsiveness

    def _heartbeat(self):
        store = get_store()
        store.touch_device(self.device_id, time.time())

    def _process_jobs(self):
        store = get_store()
        job = store.pop_next_job(self.device_id)
        if not job:
            return

        job_id = job.get("job_id")
        command = job.get("command", {})
        cmd_name = command.get("name")
        cmd_args = command.get("args", {})
        
        logger.info(f"Virtual device received command: {cmd_name} {cmd_args}")
        
        message = ""
        
        if cmd_name == "turn_on":
            self.state["power"] = "on"
            message = "電源をオンにしました"
        elif cmd_name == "turn_off":
            self.state["power"] = "off"
            self.state["motor_status"] = "stopped"  # Power off kills motor too usually
            message = "電源をオフにしました"
        elif cmd_name == "set_color":
            color = cmd_args.get("color", "white")
            self.state["color"] = color
            message = f"色を{color}に変更しました"
        elif cmd_name == "set_display":
            text = cmd_args.get("text", "")
            self.state["display_text"] = text
            message = f"ディスプレイに「{text}」を表示しました"
        elif cmd_name == "control_motor":
            action = cmd_args.get("action", "start")
            if action == "stop":
                self.state["motor_status"] = "stopped"
                message = "モーターを停止しました"
            else:
                self.state["motor_status"] = "running"
                message = "モーターを回転させました"
        elif cmd_name == "get_status":
            message = "状態を取得しました"
        else:
            message = f"コマンド {cmd_name} を実行しました (仮想)"
            
        result_payload = {
            "ok": True, 
            "ts": time.time(),
            "return_value": {
                "message": message,
                "state": self.state
            }
        }

        store.record_job_result(
            device_id=self.device_id,
            job_id=job_id,
            result_record=result_payload,
            command=command
        )
