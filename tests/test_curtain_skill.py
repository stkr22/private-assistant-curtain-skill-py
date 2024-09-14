import unittest
from unittest.mock import Mock, patch

import jinja2
import sqlmodel
from private_assistant_commons import messages

from private_assistant_curtain_skill import models
from private_assistant_curtain_skill.curtain_skill import Action, CurtainSkill, Parameters


class TestCurtainSkill(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Set up an in-memory SQLite database
        cls.engine = sqlmodel.create_engine("sqlite:///:memory:", echo=False)
        sqlmodel.SQLModel.metadata.create_all(cls.engine)

    def setUp(self):
        # Create a new session for each test
        self.session = sqlmodel.Session(self.engine)

        # Mock the MQTT client and other dependencies
        self.mock_mqtt_client = Mock()
        self.mock_config = Mock()
        self.mock_template_env = Mock(spec=jinja2.Environment)  # Correct mock with spec

        # Create an instance of CurtainSkill using the in-memory DB and mocked dependencies
        self.skill = CurtainSkill(
            config_obj=self.mock_config,
            mqtt_client=self.mock_mqtt_client,
            db_engine=self.engine,
            template_env=self.mock_template_env,
        )

    def tearDown(self):
        # Clean up the session after each test
        self.session.close()

    def test_get_devices(self):
        # Insert a mock device into the in-memory SQLite database
        mock_device = models.CurtainSkillDevice(
            id=1,
            topic="livingroom/curtain/main",
            alias="main curtain",
            room="livingroom",
            payload_open='{"state": "OPEN"}',
            payload_close='{"state": "CLOSE"}',
            payload_set_template='{"position": {{ position }}}',
        )
        with self.session as session:
            session.add(mock_device)
            session.commit()

        # Fetch devices for the "livingroom"
        devices = self.skill.get_devices("livingroom")

        # Assert that the correct device is returned
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].alias, "main curtain")
        self.assertEqual(devices[0].topic, "livingroom/curtain/main")

    def test_find_parameters(self):
        # Insert two mock devices into the in-memory SQLite database
        mock_device_1 = models.CurtainSkillDevice(
            topic="livingroom/curtain/main",
            alias="main curtain",
            room="livingroom",
            payload_open='{"state": "OPEN"}',
            payload_close='{"state": "CLOSE"}',
            payload_set_template='{"position": {{ position }}}',
        )
        mock_device_2 = models.CurtainSkillDevice(
            topic="livingroom/curtain/side",
            alias="side curtain",
            room="livingroom",
            payload_open='{"state": "OPEN"}',
            payload_close='{"state": "CLOSE"}',
            payload_set_template='{"position": {{ position }}}',
        )

        mock_intent_result = Mock(spec=messages.IntentAnalysisResult)

        # Create a mock for the `client_request` attribute
        mock_client_request = Mock()
        mock_client_request.room = "livingroom"
        mock_intent_result.client_request = mock_client_request
        mock_intent_result.nouns = ["curtain"]  # Example of provided noun
        mock_intent_result.numbers = [Mock(number_token=50)]  # Setting position to 50

        with patch.object(self.skill, "get_devices", return_value=[mock_device_1, mock_device_2]):
            # Find parameters for setting the curtain position
            parameters = self.skill.find_parameters(Action.SET, mock_intent_result)

        # Assert that all devices in the room are included in the parameters
        self.assertEqual(len(parameters.targets), 2)
        self.assertEqual(parameters.targets[0].alias, "main curtain")
        self.assertEqual(parameters.targets[1].alias, "side curtain")
        self.assertEqual(parameters.position, 50)

    def test_calculate_certainty_with_curtain(self):
        mock_intent_result = Mock(spec=messages.IntentAnalysisResult)
        mock_intent_result.nouns = ["curtain"]
        certainty = self.skill.calculate_certainty(mock_intent_result)
        self.assertEqual(certainty, 1.0)

    def test_calculate_certainty_without_curtain(self):
        mock_intent_result = Mock(spec=messages.IntentAnalysisResult)
        mock_intent_result.nouns = ["blind"]  # No "curtain"
        certainty = self.skill.calculate_certainty(mock_intent_result)
        self.assertEqual(certainty, 0)

    @patch("private_assistant_curtain_skill.curtain_skill.logger")
    def test_send_mqtt_command(self, mock_logger):
        # Create mock device
        mock_device = models.CurtainSkillDevice(
            id=1,
            topic="livingroom/curtain/main",
            alias="main curtain",
            room="livingroom",
            payload_open='{"state": "OPEN"}',
            payload_close='{"state": "CLOSE"}',
            payload_set_template='{"position": {{ position }}}',
        )

        # Mock parameters for setting the position
        parameters = Parameters(targets=[mock_device], position=75)

        # Call the method to send the MQTT command (for setting position)
        self.skill.send_mqtt_command(Action.SET, parameters)

        # Assert that the MQTT client sent the correct payload to the correct topic
        self.mock_mqtt_client.publish.assert_called_once_with("livingroom/curtain/main", '{"position": 75}', qos=1)
        mock_logger.info.assert_called_with(
            "Sending payload %s to topic %s via MQTT.", '{"position": 75}', "livingroom/curtain/main"
        )

    def test_process_request_with_set_action(self):
        mock_device = models.CurtainSkillDevice(
            id=1,
            topic="livingroom/curtain/main",
            alias="curtain",
            room="livingroom",
            payload_open='{"state": "OPEN"}',
            payload_close='{"state": "CLOSE"}',
            payload_set_template='{"position": {{ position }}}',
        )
        # Mock the client request
        mock_client_request = Mock()
        mock_client_request.room = "livingroom"
        mock_client_request.text = "set the curtain to 50%"

        # Mock the IntentAnalysisResult with spec
        mock_intent_result = Mock(spec=messages.IntentAnalysisResult)
        mock_intent_result.client_request = mock_client_request
        mock_intent_result.verbs = ["set"]
        mock_intent_result.nouns = ["curtain"]
        mock_intent_result.numbers = [Mock(number_token=50)]  # Setting position to 50

        # Set up mock parameters and method patches
        mock_parameters = Parameters(targets=[mock_device], position=50)

        with (
            patch.object(self.skill, "get_answer", return_value="Setting curtain to 50%") as mock_get_answer,
            patch.object(self.skill, "send_mqtt_command") as mock_send_mqtt_command,
            patch.object(self.skill, "find_parameters", return_value=mock_parameters),
            patch.object(self.skill, "add_text_to_output_topic") as mock_add_text_to_output_topic,
        ):
            # Execute the process_request method
            self.skill.process_request(mock_intent_result)

            # Assert that methods were called with expected arguments
            mock_get_answer.assert_called_once_with(Action.SET, mock_parameters)
            mock_send_mqtt_command.assert_called_once_with(Action.SET, mock_parameters)
            mock_add_text_to_output_topic.assert_called_once_with(
                "Setting curtain to 50%", client_request=mock_intent_result.client_request
            )
