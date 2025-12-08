import asyncio
import json
import logging
from typing import Any, List, Optional
from mcp.server import Server
from mcp.types import (
    Resource,
    Tool,
    TextContent,
    ImageContent,
    EmbeddedResource
)
import mcp.types as types
from iot_agent.state import _DEVICES, _PENDING_JOBS, _JOB_METADATA, _COMPLETED_JOBS
from iot_agent.device_utils import _enqueue_device_command, _await_device_result, _await_device_result_async, _device_supports_capability, _serialize_device

# Create the server instance
mcp_server = Server("iot-agent")

@mcp_server.list_resources()
async def list_resources() -> list[Resource]:
    resources = []
    for device_id, device in _DEVICES.items():
        display_name = device.meta.get("display_name", device_id)
        resources.append(
            Resource(
                uri=f"iot://{device_id}/status",
                name=f"{display_name} Status",
                description=f"Status and capabilities of {device_id}",
                mimeType="application/json",
            )
        )
    return resources

@mcp_server.read_resource()
async def read_resource(uri: str) -> str | bytes:
    # uri format: iot://{device_id}/status
    if not uri.startswith("iot://") or not uri.endswith("/status"):
        raise ValueError(f"Invalid URI: {uri}")
    
    parts = uri.replace("iot://", "").split("/")
    if len(parts) < 2:
        raise ValueError(f"Invalid URI: {uri}")
        
    device_id = parts[0]
    device = _DEVICES.get(device_id)
    if not device:
        raise ValueError(f"Device not found: {device_id}")
        
    return json.dumps(_serialize_device(device), ensure_ascii=False)

@mcp_server.list_tools()
async def list_tools() -> list[Tool]:
    tools = []
    
    device_ids = list(_DEVICES.keys())
    
    # Generic control tool with dynamic device_id enum
    tools.append(Tool(
        name="control_device",
        description="IoTデバイスを制御します。単一のコマンド、または複数のコマンドを（順次または同時）実行できます。",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string", 
                    "description": "制御対象のデバイスID",
                    "enum": device_ids if device_ids else ["no_devices_available"]
                },
                "command": {"type": "string", "description": "単一実行時のコマンド名（commandsを指定しない場合は必須）"},
                "args": {"type": "object", "description": "単一実行時の引数"},
                "commands": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "args": {"type": "object"}
                        },
                        "required": ["name"]
                    },
                    "description": "複数コマンドのリスト（例: [{'name': 'led0_on', 'args': {}}, {'name': 'buzzer_tone', 'args': {'freq_hz': 1000}}]）"
                },
                "mode": {
                    "type": "string",
                    "enum": ["sequential", "parallel"],
                    "default": "sequential",
                    "description": "複数コマンドの実行モード。sequential: 順番に実行, parallel: 同時実行（可能な場合、特にブザーとモーターの同時実行に使用）"
                }
            },
            "required": ["device_id"]
        }
    ))
    
    tools.append(Tool(
        name="get_device_list",
        description="現在登録されているIoTデバイスの一覧と、それぞれの機能（capabilities）を取得します。",
        inputSchema={
            "type": "object",
            "properties": {},
        }
    ))

    return tools

@mcp_server.call_tool()
async def call_tool(name: str, arguments: Any) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    if name == "get_device_list":
        devices = [_serialize_device(d) for d in _DEVICES.values()]
        return [TextContent(type="text", text=json.dumps(devices, ensure_ascii=False, indent=2))]

    if name == "control_device":
        device_id = arguments.get("device_id")
        command = arguments.get("command")
        args = arguments.get("args", {})
        commands = arguments.get("commands")
        mode = arguments.get("mode", "sequential")
        
        if not device_id:
             return [TextContent(type="text", text="エラー: device_id は必須です")]
        
        device = _DEVICES.get(device_id)
        if not device:
             return [TextContent(type="text", text=f"エラー: デバイス '{device_id}' が見つかりません")]
             
        job_payload = None
        if commands and isinstance(commands, list) and len(commands) > 0:
            job_payload = {
                "name": "run_sequence",
                "args": {"commands": commands, "mode": mode}
            }
        elif command:
            if not _device_supports_capability(device, command):
                 return [TextContent(type="text", text=f"エラー: デバイス {device_id} は '{command}' をサポートしていません")]
            job_payload = {"name": command, "args": args}
        else:
             return [TextContent(type="text", text="エラー: command または commands の指定が必要です")]

        # Enqueue job
        job_id = _enqueue_device_command(device_id, job_payload, source="mcp")
        if not job_id:
             return [TextContent(type="text", text="エラー: コマンドをキューに追加できませんでした（デバイスが切断されている可能性があります）")]

        # Wait for result
        # Note: this blocks the async loop handling this request.
        result = await _await_device_result_async(device_id, job_id, timeout=10.0)
        
        if result:
            return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
        else:
            return [TextContent(type="text", text="コマンドはキューに入れられましたが、結果待ちでタイムアウトしました。")]
            
    raise ValueError(f"Tool not found: {name}")
