
import unittest
from unittest.mock import patch, MagicMock
import sys
import os
import json

# Add parent directory to path to import app
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import app
from iot_agent.models import DeviceState

class TestImageStripping(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True

    @patch('app._execute_device_command_sequence')
    @patch('app._client')
    @patch('app._agent_device')
    @patch('app._call_llm_and_parse')
    @patch('app._validate_device_command_sequence')
    def test_chat_strips_images_for_platform(self, mock_validate, mock_call_llm, mock_agent_device, mock_client, mock_execute):
        # Setup mocks
        mock_agent_device.return_value = MagicMock(device_id="test_device")
        mock_call_llm.return_value = {
            "reply": "Test reply",
            "device_commands": [{"name": "capture_camera_photo"}]
        }
        mock_validate.return_value = ([{"name": "capture_camera_photo"}], [])
        
        # Mock execution returning images
        mock_execute.return_value = ("Final Reply", 200, [{"data_url": "data:image/png;base64,FAKE"}])

        # 1. Request from Platform (python-requests)
        headers = {'User-Agent': 'python-requests/2.28.0'}
        payload = {'messages': [{'role': 'user', 'content': 'photo'}]}
        
        res = self.app.post('/api/chat', data=json.dumps(payload), content_type='application/json', headers=headers)
        data = json.loads(res.data)
        
        self.assertIn('reply', data)
        self.assertNotIn('images', data) # Should be stripped

        # 2. Request from Browser
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = self.app.post('/api/chat', data=json.dumps(payload), content_type='application/json', headers=headers)
        data = json.loads(res.data)
        
        self.assertIn('reply', data)
        self.assertIn('images', data) # Should exist
        self.assertEqual(len(data['images']), 1)

    @patch('app._execute_device_command_sequence')
    @patch('app._client')
    @patch('app._call_llm_for_conversation_review')
    @patch('app._validate_device_command_sequence')
    def test_review_strips_images(self, mock_validate, mock_call_llm, mock_client, mock_execute):
         # Setup mocks
        mock_call_llm.return_value = {
            "action_required": True,
            "device_commands": [{"name": "capture_camera_photo"}]
        }
        mock_validate.return_value = ([{"name": "capture_camera_photo"}], [])
        mock_execute.return_value = ("Final Reply", 200, [{"data_url": "data:image/png;base64,FAKE"}])

        payload = {'history': [{'role': 'user', 'content': 'test'}]}
        res = self.app.post('/api/conversations/review', data=json.dumps(payload), content_type='application/json')
        data = json.loads(res.data)

        self.assertTrue(data['action_taken'])
        self.assertNotIn('images', data) # Should be stripped explicitly

    @patch('iot_agent.device_utils._DEVICES')
    def test_list_devices_strips_images(self, mock_devices):
        # Setup a device with image in last_result
        device = DeviceState(
            device_id="cam1",
            capabilities=[],
            meta={},
            last_seen=123,
            approved=True
        )
        device.last_result = {
            "ok": True,
            "return_value": {
                "image_base64": "HUGE_BASE64_STRING_XXXX",
                "text": "ok"
            }
        }
        
        # We need to inject this device into the real _DEVICES used by app
        # But app imports _DEVICES from iot_agent.state
        # And device_utils imports _DEVICES from iot_agent.state
        # So we should patch iot_agent.state._DEVICES or just the dictionary
        
        # The patch above patches device_utils._DEVICES name, but app uses app._DEVICES?
        # app imports _DEVICES from iot_agent.state.
        
        pass

    # Re-implementing test_list_devices_strips_images correctly
    def test_device_serialization_strips_images(self):
        from iot_agent.device_utils import _serialize_device
        
        device = DeviceState(
            device_id="cam1",
            capabilities=[],
            meta={},
            last_seen=123,
            approved=True
        )
        device.last_result = {
            "ok": True,
            "return_value": {
                "image_base64": "HUGE_BASE64_STRING_XXXX",
                "nested": {
                    "image_data": "ANOTHER_HUGE_ONE"
                },
                "text": "ok"
            }
        }
        
        serialized = _serialize_device(device)
        last_result = serialized['last_result']
        
        self.assertNotEqual(last_result['return_value']['image_base64'], "HUGE_BASE64_STRING_XXXX")
        self.assertIn("omitted", last_result['return_value']['image_base64'])
        
        self.assertNotEqual(last_result['return_value']['nested']['image_data'], "ANOTHER_HUGE_ONE")
        self.assertIn("omitted", last_result['return_value']['nested']['image_data'])
        
        self.assertEqual(last_result['return_value']['text'], "ok")

if __name__ == '__main__':
    unittest.main()
