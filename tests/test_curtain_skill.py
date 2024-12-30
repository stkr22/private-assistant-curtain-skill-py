import logging
import unittest
from unittest.mock import AsyncMock, Mock, patch

import jinja2
from private_assistant_commons import messages
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from private_assistant_curtain_skill import models
from private_assistant_curtain_skill.curtain_skill import Action, CurtainSkill, Parameters


class TestCurtainSkill(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        # Set up an in-memory SQLite database for async usage
        cls.engine_async = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async def asyncSetUp(self):
        # Create tables asynchronously before each test
        async with self.engine_async.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

        # Create mock components for testing
        self.mock_mqtt_client = AsyncMock()
        self.mock_config = Mock()
        self.mock_template_env = Mock(spec=jinja2.Environment)
        self.mock_task_group = AsyncMock()
        self.mock_logger = Mock(logging.Logger)

        # Create an instance of CurtainSkill using the in-memory DB and mocked dependencies
        self.skill = CurtainSkill(
            config_obj=self.mock_config,
            mqtt_client=self.mock_mqtt_client,
            db_engine=self.engine_async,
            template_env=self.mock_template_env,
            task_group=self.mock_task_group,
            logger=self.mock_logger,
        )

    async def asyncTearDown(self):
        # Drop tables asynchronously after each test to ensure a clean state
        async with self.engine_async.begin() as conn:
            await conn.run_sync(SQLModel.metadata.drop_all)

    async def test_get_devices(self):
        # Insert mock devices into the in-memory SQLite database
        mock_device_1 = models.CurtainSkillDevice(
            id=1,
            topic="livingroom/curtain/main",
            alias="main curtain",
            room="living room",
            payload_open='{"state": "OPEN"}',
            payload_close='{"state": "CLOSE"}',
            payload_set_template='{"position": {{ position }}}',
        )
        mock_device_2 = models.CurtainSkillDevice(
            id=2,
            topic="bedroom/curtain/main",
            alias="main curtain",
            room="bedroom",
            payload_open='{"state": "OPEN"}',
            payload_close='{"state": "CLOSE"}',
            payload_set_template='{"position": {{ position }}}',
        )
        mock_device_3 = models.CurtainSkillDevice(
            id=3,
            topic="kitchen/curtain/main",
            alias="main curtain",
            room="kitchen",
            payload_open='{"state": "OPEN"}',
            payload_close='{"state": "CLOSE"}',
            payload_set_template='{"position": {{ position }}}',
        )
        async with AsyncSession(self.engine_async) as session, session.begin():
            session.add_all([mock_device_1, mock_device_2, mock_device_3])

        devices = await self.skill.get_devices(["living room"])

        # Assert that the correct device is returned
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].alias, "main curtain")
        self.assertEqual(devices[0].topic, "livingroom/curtain/main")

        devices = await self.skill.get_devices(["living room", "bedroom"])

        # Assert that the correct device is returned
        self.assertEqual(len(devices), 2)
        self.assertEqual(devices[0].alias, "main curtain")
        self.assertEqual(devices[0].topic, "livingroom/curtain/main")
        self.assertEqual(devices[1].alias, "main curtain")
        self.assertEqual(devices[1].topic, "bedroom/curtain/main")

    async def test_find_parameters(self):
        # Insert mock devices into the in-memory SQLite database
        mock_device_1 = models.CurtainSkillDevice(
            topic="livingroom/curtain/main",
            alias="main curtain",
            room="living room",
            payload_open='{"state": "OPEN"}',
            payload_close='{"state": "CLOSE"}',
            payload_set_template='{"position": {{ position }}}',
        )
        mock_device_2 = models.CurtainSkillDevice(
            topic="livingroom/curtain/side",
            alias="side curtain",
            room="living room",
            payload_open='{"state": "OPEN"}',
            payload_close='{"state": "CLOSE"}',
            payload_set_template='{"position": {{ position }}}',
        )
        mock_device_3 = models.CurtainSkillDevice(
            id=3,
            topic="kitchen/curtain/main",
            alias="main curtain",
            room="kitchen",
            payload_open='{"state": "OPEN"}',
            payload_close='{"state": "CLOSE"}',
            payload_set_template='{"position": {{ position }}}',
        )

        async with AsyncSession(self.engine_async) as session, session.begin():
            session.add_all([mock_device_1, mock_device_2, mock_device_3])

        mock_intent_result = Mock(spec=messages.IntentAnalysisResult)
        mock_client_request = Mock(spec=messages.ClientRequest)
        mock_client_request.room = "living room"
        mock_intent_result.rooms = []
        mock_intent_result.client_request = mock_client_request
        mock_intent_result.nouns = ["curtain"]
        mock_intent_result.numbers = [Mock(number_token=50)]

        parameters = await self.skill.find_parameters(Action.SET, mock_intent_result)

        # Assert that all devices in the room are included in the parameters
        self.assertEqual(len(parameters.targets), 2)
        self.assertEqual(parameters.targets[0].alias, "main curtain")
        self.assertEqual(parameters.targets[1].alias, "side curtain")
        self.assertEqual(parameters.position, 50)

    async def test_calculate_certainty_with_curtain(self):
        mock_intent_result = Mock(spec=messages.IntentAnalysisResult)
        mock_intent_result.nouns = ["curtain"]
        certainty = await self.skill.calculate_certainty(mock_intent_result)
        self.assertEqual(certainty, 1.0)

    async def test_calculate_certainty_without_curtain(self):
        mock_intent_result = Mock(spec=messages.IntentAnalysisResult)
        mock_intent_result.nouns = ["blind"]
        certainty = await self.skill.calculate_certainty(mock_intent_result)
        self.assertEqual(certainty, 0)

    async def test_send_mqtt_command(self):
        # Create mock device
        mock_device = models.CurtainSkillDevice(
            id=1,
            topic="livingroom/curtain/main",
            alias="main curtain",
            room="living room",
            payload_open='{"state": "OPEN"}',
            payload_close='{"state": "CLOSE"}',
            payload_set_template='{"position": {{ position }}}',
        )

        parameters = Parameters(targets=[mock_device], position=75)

        # Call the async method to send the MQTT command
        await self.skill.send_mqtt_command(Action.SET, parameters)

        # Assert that the MQTT client sent the correct payload
        self.mock_mqtt_client.publish.assert_called_once_with("livingroom/curtain/main", '{"position": 75}', qos=1)
        self.mock_logger.info.assert_called_with(
            "Sending payload %s to topic %s via MQTT.", '{"position": 75}', "livingroom/curtain/main"
        )

    async def test_process_request_with_set_action(self):
        mock_device = models.CurtainSkillDevice(
            id=1,
            topic="livingroom/curtain/main",
            alias="curtain",
            room="living room",
            payload_open='{"state": "OPEN"}',
            payload_close='{"state": "CLOSE"}',
            payload_set_template='{"position": {{ position }}}',
        )
        mock_client_request = Mock()
        mock_client_request.room = "living room"
        mock_client_request.text = "set the curtain to 50%"

        mock_intent_result = Mock(spec=messages.IntentAnalysisResult)
        mock_intent_result.client_request = mock_client_request
        mock_intent_result.verbs = ["set"]
        mock_intent_result.nouns = ["curtain"]
        mock_intent_result.numbers = [Mock(number_token=50)]

        mock_parameters = Parameters(targets=[mock_device], position=50)

        with (
            patch.object(self.skill, "get_answer", return_value="Setting curtain to 50%") as mock_get_answer,
            patch.object(self.skill, "send_mqtt_command") as mock_send_mqtt_command,
            patch.object(self.skill, "find_parameters", return_value=mock_parameters),
            patch.object(self.skill, "send_response") as mock_send_response,
        ):
            await self.skill.process_request(mock_intent_result)

            # Assert that methods were called with expected arguments
            mock_get_answer.assert_called_once_with(Action.SET, mock_parameters)
            mock_send_mqtt_command.assert_called_once_with(Action.SET, mock_parameters)
            mock_send_response.assert_called_once_with(
                "Setting curtain to 50%", client_request=mock_intent_result.client_request
            )
