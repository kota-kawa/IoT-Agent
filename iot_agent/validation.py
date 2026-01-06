from typing import Any, Dict, List, Optional, Tuple

from .device_utils import _first_device_id
from .storage import get_store


def _validate_device_command(command: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    # LLM から生成された device_command の妥当性を検査しメッセージを返す
    if not isinstance(command, dict):
        return None, "device_command の形式が不正なため処理を中止しました。"

    def _coerce_sequence_group(raw_value: Any) -> Optional[int]:
        if isinstance(raw_value, int) and raw_value > 0:
            return raw_value
        if isinstance(raw_value, str) and raw_value.strip().isdigit():
            candidate = int(raw_value.strip())
            if candidate > 0:
                return candidate
        return None

    raw_device_id = command.get("device_id")
    devices = get_store().list_devices()
    device_ids = {device.device_id for device in devices}
    device_id: Optional[str] = None
    if isinstance(raw_device_id, str) and raw_device_id.strip():
        device_id = raw_device_id.strip()
    elif len(devices) == 1:
        device_id = _first_device_id()

    if not device_id:
        if devices:
            return None, "複数のデバイスが登録されているため、device_id を指定できないコマンドは実行しません。"
        return None, "実行可能なデバイスが登録されていません。"

    if device_id not in device_ids:
        return None, f"不明な device_id '{device_id}' が指定されたため処理を中止しました。"

    name = command.get("name")
    if not isinstance(name, str) or not name.strip():
        return None, "device_command の name が空です。"

    args = command.get("args")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return None, "device_command の args はオブジェクトである必要があります。"

    validated_name = name.strip()
    device = get_store().get_device(device_id)
    capability_names = {
        str(cap.get("name")).strip()
        for cap in (device.capabilities if device else [])
        if isinstance(cap, dict) and cap.get("name")
    }
    sequence_group = _coerce_sequence_group(command.get("sequence_group"))
    if capability_names and validated_name not in capability_names:
        return (
            None,
            f"{device_id} は '{validated_name}' という機能をサポートしていないため実行を中止しました。",
        )

    validated = {
        "device_id": device_id,
        "name": validated_name,
        "args": args,
    }
    if sequence_group:
        validated["sequence_group"] = sequence_group
    return validated, None


def _validate_device_command_sequence(
    commands: Any,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    # 複数コマンドを検証し、成功したものとエラー文をそれぞれ返す
    if commands is None:
        return [], []

    if isinstance(commands, dict):
        command_items: List[Any] = [commands]
    elif isinstance(commands, list):
        command_items = list(commands)
    else:
        return [], ["device_commands の形式が不正なため処理を中止しました。"]

    validated_commands: List[Dict[str, Any]] = []
    errors: List[str] = []

    for index, raw_command in enumerate(command_items, start=1):
        validated, error = _validate_device_command(raw_command)
        if validated:
            validated_commands.append(validated)
            continue
        message = error or "device_command の形式が不正なため処理を中止しました。"
        errors.append(f"ステップ{index}: {message}")

    return validated_commands, errors
