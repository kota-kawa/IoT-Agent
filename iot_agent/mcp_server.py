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
    
    # Generic control tool
    tools.append(Tool(
        name="control_device",
        description="IoTデバイスを制御します。電源のオン/オフ、色の設定、写真撮影などのアクションを実行するために使用します。",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "制御対象のデバイスID"},
                "command": {"type": "string", "description": "機能/コマンド名（例: 'turn_on', 'set_color', 'set_brightness'）"},
                "args": {"type": "object", "description": "コマンドの引数（例: {'duration': 5}）"}
            },
            "required": ["device_id", "command"]
        }
    ))

    # We could also dynamically expose specific tools if needed, but generic is flexible.
    return tools

@mcp_server.call_tool()
async def call_tool(name: str, arguments: Any) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    if name == "control_device":
        device_id = arguments.get("device_id")
        command = arguments.get("command")
        args = arguments.get("args", {})
        
        if not device_id or not command:
             return [TextContent(type="text", text="エラー: device_id と command は必須です")]
        
        device = _DEVICES.get(device_id)
        if not device:
             return [TextContent(type="text", text="エラー: デバイスが見つかりません")]
             
        # Use internal validation helper? Or just _device_supports_capability
        if not _device_supports_capability(device, command):
             return [TextContent(type="text", text=f"エラー: デバイス {device_id} は '{command}' をサポートしていません")]
             
        # Enqueue job
        job_id = _enqueue_device_command(device_id, {"name": command, "args": args}, source="mcp")
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
