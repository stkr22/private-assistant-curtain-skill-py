import unittest
from unittest.mock import Mock, patch

import jinja2
from homeassistant_api import Entity, Group, State
from private_assistant_commons import messages
from private_assistant_curtain_skill.curtain_skill import Action, CurtainSkill, Parameters


class TestCurtainSkill(unittest.TestCase):
    def setUp(self):
        self.mock_mqtt_client = Mock()
        self.mock_config = Mock()
        self.mock_ha_api_client = Mock()
        self.mock_template_env = Mock(spec=jinja2.Environment)

        self.skill = CurtainSkill(
            config_obj=self.mock_config,
            mqtt_client=self.mock_mqtt_client,
            ha_api_client=self.mock_ha_api_client,
            template_env=self.mock_template_env,
        )

    def test_calculate_certainty_with_curtain(self):
        mock_intent_result = Mock(spec=messages.IntentAnalysisResult)
        mock_intent_result.nouns = ["curtain"]
        certainty = self.skill.calculate_certainty(mock_intent_result)
        self.assertEqual(certainty, 1.0)

    def test_calculate_certainty_without_curtain(self):
        mock_intent_result = Mock(spec=messages.IntentAnalysisResult)
        mock_intent_result.nouns = ["window"]
        certainty = self.skill.calculate_certainty(mock_intent_result)
        self.assertEqual(certainty, 0)

    def test_get_targets(self):
        # Mock the State object with a concrete state value
        mock_state = Mock(spec=State)
        mock_state.state = "open"
        mock_state.attributes = {"friendly_name": "Living Room Curtain"}

        # Mock the Entity object that contains the State
        mock_entity = Mock(spec=Entity)
        mock_entity.state = mock_state

        # Mock the Group object that contains the Entity
        mock_group = Mock(spec=Group)
        mock_group.entities = {"entity_id_1": mock_entity}

        # Mock the ha_api_client to return the Group
        self.skill.ha_api_client.get_entities.return_value = {"cover": mock_group}

        # Call the method and check the result
        targets = self.skill.get_targets()

        # Assert that the returned targets dictionary contains the correct state string
        self.assertIn("entity_id_1", targets)
        self.assertEqual(targets["entity_id_1"], mock_state)

    def test_find_parameter_targets(self):
        self.skill._target_alias_cache = {
            "livingroom/curtain/main": "Curtain",
            "bedroom/curtain/main": "Curtain",
            "kitchen/curtain/backup": "Curtain",
        }
        targets = self.skill.find_parameter_targets("livingroom")
        self.assertEqual(targets, ["livingroom/curtain/main"])

    def test_get_answer(self):
        # Set up mock template and return value
        mock_template = Mock()
        mock_template.render.return_value = "Opening curtain in living room"

        # Ensure action_to_answer is a dictionary with Action keys and Mock templates as values
        self.skill.action_to_answer = {
            Action.OPEN: mock_template,
            Action.CLOSE: mock_template,
            Action.SET: mock_template,
        }

        # Mock the State object
        mock_state = Mock(spec=State)
        mock_state.entity_id = "livingroom/curtain/main"
        mock_state.state = "open"
        mock_state.attributes = {"friendly_name": "Living Room Curtain"}

        # Mock the Entity object that contains the State
        mock_entity = Mock(spec=Entity)
        mock_entity.slug = "curtain/main"
        mock_entity.state = mock_state
        mock_entity.entity_id = "livingroom/curtain/main"

        # Mock the Group object that contains the Entity
        mock_group = Mock(spec=Group)
        mock_group.entities = {"entity_id_1": mock_entity}

        # Mock the ha_api_client to return the Group
        self.skill.ha_api_client.get_entities.return_value = {"cover": mock_group}

        # Force the cache to be built by accessing it
        _ = self.skill.target_alias_cache  # This builds the alias cache using the mocked data

        # Define the parameters and action
        mock_parameters = Parameters(position=0, targets=["livingroom/curtain/main"])

        # Call the method and check the result
        answer = self.skill.get_answer(Action.OPEN, mock_parameters)

        # Assert the answer is as expected
        self.assertEqual(answer, "Opening curtain in living room")

        # Ensure that the template's render method was called with the correct parameters
        mock_template.render.assert_called_once_with(
            action=Action.OPEN, parameters=mock_parameters, target_alias_cache=self.skill.target_alias_cache
        )

    @patch("private_assistant_curtain_skill.curtain_skill.logger")
    def test_call_action_api(self, mock_logger):
        mock_service = Mock()
        self.skill.ha_api_client.get_domain.return_value = mock_service

        parameters = Parameters(targets=["livingroom/curtain/main"])
        self.skill.call_action_api(Action.OPEN, parameters)

        mock_service.open_cover.assert_called_once_with(entity_id="livingroom/curtain/main")
        mock_logger.error.assert_not_called()

    def test_process_request_with_valid_action(self):
        mock_client_request = Mock()
        mock_client_request.room = "living room"

        mock_intent_result = Mock(spec=messages.IntentAnalysisResult)
        mock_intent_result.verbs = ["open"]
        mock_intent_result.client_request = mock_client_request

        mock_parameters = Parameters(targets=["livingroom/curtain/main"])

        with (
            patch.object(self.skill, "get_answer", return_value="Opening curtain in living room") as mock_get_answer,
            patch.object(self.skill, "call_action_api") as mock_call_action_api,
            patch.object(self.skill, "find_parameter_targets", return_value=["livingroom/curtain/main"]),
            patch.object(self.skill, "add_text_to_output_topic") as mock_add_text_to_output_topic,
        ):
            self.skill.process_request(mock_intent_result)

            mock_get_answer.assert_called_once_with(Action.OPEN, mock_parameters)
            mock_call_action_api.assert_called_once_with(Action.OPEN, mock_parameters)
            mock_add_text_to_output_topic.assert_called_once_with(
                "Opening curtain in living room", client_request=mock_intent_result.client_request
            )
