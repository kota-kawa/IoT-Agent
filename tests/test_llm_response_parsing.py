import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import _call_llm_and_parse


class _FakeResponses:
    def __init__(self, text: str):
        self._text = text

    def create(self, **_kwargs):
        class _FakeResponse:
            def __init__(self, text: str):
                self.output_text = text

        return _FakeResponse(self._text)


class _FakeClient:
    def __init__(self, text: str):
        self.responses = _FakeResponses(text)


@pytest.mark.parametrize(
    "raw_text, expected_reply",
    [
        (
            "内部温度センサーで現在の温度を測定しますね。少々お待ちください。\n"
            '{"reply": "ピコの内部温度センサーで現在の温度を測定します。少々お待ちください。",'
            ' "device_commands": [{"device_id": "dev-1", "name": "temp", "args": {}}]}',
            "ピコの内部温度センサーで現在の温度を測定します。少々お待ちください。",
        ),
        (
            '{"reply": "Ready", "device_commands": null}\n追伸: thanks!',
            "Ready",
        ),
    ],
)
def test_call_llm_and_parse_extracts_json_with_extra_text(raw_text, expected_reply):
    client = _FakeClient(raw_text)
    result = _call_llm_and_parse(client, messages=[{"role": "user", "content": "test"}])

    assert result["reply"] == expected_reply
    assert isinstance(result.get("device_commands"), list)

    if '"device_commands": null' not in raw_text and "device_commands" in raw_text:
        assert result["device_commands"]
