import logging
import unittest
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import jinja2
from private_assistant_commons import (
    ClassifiedIntent,
    ClientRequest,
    Entity,
    EntityType,
    IntentRequest,
    IntentType,
)
from private_assistant_commons.database.models import DeviceType, GlobalDevice, Room

from private_assistant_curtain_skill.curtain_skill import CurtainSkill


def create_mock_global_device(  # noqa: PLR0913
    device_id: uuid.UUID | None = None,
    name: str = "Test Curtain",
    device_type_name: str = "curtain",
    room_name: str = "living room",
    topic: str = "livingroom/curtain/main",
    payload_open: str = '{"state": "OPEN"}',
    payload_close: str = '{"state": "CLOSE"}',
    payload_set_template: str = '{"position": {{ position }}}',
) -> Mock:
    """
    Create a mock GlobalDevice for testing.

    This avoids SQLAlchemy initialization issues by using Mock objects.
    """
    # Create mock device type
    mock_device_type = Mock(spec=DeviceType)
    mock_device_type.id = uuid.uuid4()
    mock_device_type.name = device_type_name

    # Create mock room
    mock_room = Mock(spec=Room)
    mock_room.id = uuid.uuid4()
    mock_room.name = room_name

    # Create mock global device
    mock_device = Mock(spec=GlobalDevice)
    mock_device.id = device_id or uuid.uuid4()
    mock_device.name = name
    mock_device.device_type = mock_device_type
    mock_device.device_type_id = mock_device_type.id
    mock_device.room = mock_room
    mock_device.room_id = mock_room.id
    mock_device.pattern = [name.lower()]
    mock_device.device_attributes = {
        "topic": topic,
        "payload_open": payload_open,
        "payload_close": payload_close,
        "payload_set_template": payload_set_template,
    }
    mock_device.created_at = datetime.now(UTC)
    mock_device.updated_at = datetime.now(UTC)

    return mock_device


def create_mock_intent_request(  # noqa: PLR0913
    intent_type: IntentType,
    confidence: float = 0.9,
    devices: list[str] | None = None,
    rooms: list[str] | None = None,
    numbers: list[int] | None = None,
    current_room: str = "living room",
    text: str = "test command",
) -> IntentRequest:
    """
    Create a mock IntentRequest for testing.

    Args:
        intent_type: The type of intent
        confidence: Confidence score for the intent
        devices: List of device names mentioned
        rooms: List of room names mentioned
        numbers: List of numbers mentioned (for position)
        current_room: The room where command originated
        text: The original text command

    Returns:
        IntentRequest object for testing
    """
    # Build entities dict
    entities: dict[str, list[Entity]] = {}

    if devices:
        entities["devices"] = [
            Entity(
                id=uuid.uuid4(),
                type=EntityType.DEVICE,
                raw_text=device,
                normalized_value=device,
                confidence=0.9,
                metadata={},
                linked_to=[],
            )
            for device in devices
        ]

    if rooms:
        entities["rooms"] = [
            Entity(
                id=uuid.uuid4(),
                type=EntityType.ROOM,
                raw_text=room,
                normalized_value=room,
                confidence=1.0,
                metadata={},
                linked_to=[],
            )
            for room in rooms
        ]

    if numbers:
        entities["numbers"] = [
            Entity(
                id=uuid.uuid4(),
                type=EntityType.NUMBER,
                raw_text=str(num),
                normalized_value=num,
                confidence=1.0,
                metadata={},
                linked_to=[],
            )
            for num in numbers
        ]

    # Create ClassifiedIntent
    classified_intent = ClassifiedIntent(
        id=uuid.uuid4(),
        intent_type=intent_type,
        confidence=confidence,
        entities=entities,
        alternative_intents=[],
        raw_text=text,
        timestamp=datetime.now(UTC),
    )

    # Create ClientRequest
    client_request = ClientRequest(
        id=uuid.uuid4(),
        text=text,
        room=current_room,
        output_topic=f"assistant/response/{current_room}",
    )

    # Create and return IntentRequest
    return IntentRequest(
        id=uuid.uuid4(),
        classified_intent=classified_intent,
        client_request=client_request,
    )


class TestCurtainSkill(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        """Set up test fixtures before each test."""
        # Create mock components for testing
        self.mock_mqtt_client = AsyncMock()
        self.mock_config = Mock()
        self.mock_config.client_id = "curtain_skill_test"  # Required by BaseSkill
        self.mock_config.intent_analysis_result_topic = "test/intent"
        self.mock_config.device_update_topic = "test/device_update"
        self.mock_template_env = Mock(spec=jinja2.Environment)

        # Mock task_group with proper create_task behavior
        self.mock_task_group = Mock()

        def create_mock_task(_coro, **kwargs):  # noqa: ARG001
            mock_task = Mock()
            mock_task.add_done_callback = Mock()
            return mock_task

        self.mock_task_group.create_task = Mock(side_effect=create_mock_task)

        self.mock_logger = Mock(spec=logging.Logger)

        # Create mock templates
        self.mock_help_template = Mock()
        self.mock_help_template.render.return_value = "Help text"
        self.mock_state_template = Mock()
        self.mock_state_template.render.return_value = "Curtains opened"
        self.mock_set_template = Mock()
        self.mock_set_template.render.return_value = "Curtains set to position"

        self.mock_template_env.get_template.side_effect = lambda name: {
            "help.j2": self.mock_help_template,
            "state.j2": self.mock_state_template,
            "set_curtain.j2": self.mock_set_template,
        }[name]

        # Create mock database engine (not actually used in unit tests)
        self.mock_db_engine = Mock()

        # Create skill instance
        self.skill = CurtainSkill(
            config_obj=self.mock_config,
            mqtt_client=self.mock_mqtt_client,
            template_env=self.mock_template_env,
            task_group=self.mock_task_group,
            engine=self.mock_db_engine,
            logger=self.mock_logger,
        )

        # Mock add_task to avoid actual task execution
        self.skill.add_task = Mock()

    async def test_get_curtain_devices_single_room(self):
        """Test getting curtain devices for a single room."""
        # Create mock devices
        device1 = create_mock_global_device(name="Main Curtain", room_name="living room")
        device2 = create_mock_global_device(name="Side Curtain", room_name="living room")
        device3 = create_mock_global_device(name="Bedroom Curtain", room_name="bedroom")

        # Set global_devices
        self.skill.global_devices = [device1, device2, device3]

        # Get devices for living room
        devices = self.skill._get_curtain_devices(["living room"])

        # Assert we got the correct devices
        self.assertEqual(len(devices), 2)
        self.assertEqual(devices[0].alias, "Main Curtain")
        self.assertEqual(devices[1].alias, "Side Curtain")

    async def test_get_curtain_devices_multiple_rooms(self):
        """Test getting curtain devices for multiple rooms."""
        # Create mock devices
        device1 = create_mock_global_device(name="Living Room Curtain", room_name="living room")
        device2 = create_mock_global_device(name="Bedroom Curtain", room_name="bedroom")
        device3 = create_mock_global_device(name="Kitchen Curtain", room_name="kitchen")

        # Set global_devices
        self.skill.global_devices = [device1, device2, device3]

        # Get devices for multiple rooms
        devices = self.skill._get_curtain_devices(["living room", "bedroom"])

        # Assert we got devices from both rooms
        self.assertEqual(len(devices), 2)
        device_names = [d.alias for d in devices]
        self.assertIn("Living Room Curtain", device_names)
        self.assertIn("Bedroom Curtain", device_names)

    async def test_extract_parameters_with_room_entity(self):
        """Test parameter extraction with explicit room entity."""
        # Create mock device
        device = create_mock_global_device(name="Curtain", room_name="bedroom")
        self.skill.global_devices = [device]

        # Create intent request with room entity
        intent_request = create_mock_intent_request(
            intent_type=IntentType.DEVICE_OPEN,
            rooms=["bedroom"],
            current_room="living room",
        )

        # Extract parameters
        parameters = await self.skill._extract_parameters(intent_request)

        # Assert correct room was used
        self.assertEqual(parameters.rooms, ["bedroom"])
        self.assertEqual(len(parameters.targets), 1)
        self.assertEqual(parameters.targets[0].alias, "Curtain")

    async def test_extract_parameters_default_to_current_room(self):
        """Test parameter extraction defaults to current room when no room entity."""
        # Create mock device
        device = create_mock_global_device(name="Curtain", room_name="living room")
        self.skill.global_devices = [device]

        # Create intent request without room entity
        intent_request = create_mock_intent_request(
            intent_type=IntentType.DEVICE_OPEN,
            current_room="living room",
        )

        # Extract parameters
        parameters = await self.skill._extract_parameters(intent_request)

        # Assert current room was used
        self.assertEqual(parameters.rooms, ["living room"])
        self.assertEqual(len(parameters.targets), 1)

    async def test_extract_parameters_with_position(self):
        """Test parameter extraction with position for SET intent."""
        # Create mock device
        device = create_mock_global_device(room_name="living room")
        self.skill.global_devices = [device]

        # Create intent request with position
        intent_request = create_mock_intent_request(
            intent_type=IntentType.DEVICE_SET,
            numbers=[75],
            current_room="living room",
        )

        # Extract parameters
        parameters = await self.skill._extract_parameters(intent_request)

        # Assert position was extracted
        self.assertEqual(parameters.position, 75)
        self.assertEqual(len(parameters.targets), 1)

    async def test_process_request_device_open(self):
        """Test processing DEVICE_OPEN intent request."""
        # Create mock device
        device = create_mock_global_device(room_name="living room")
        self.skill.global_devices = [device]

        # Mock template rendering
        mock_template = Mock()
        mock_template.render.return_value = "The curtains in living room have been opened."
        self.skill.intent_to_template[IntentType.DEVICE_OPEN] = mock_template

        # Create intent request
        intent_request = create_mock_intent_request(
            intent_type=IntentType.DEVICE_OPEN,
            current_room="living room",
            text="open the curtains",
        )

        # Process request
        await self.skill.process_request(intent_request)

        # Assert response was sent
        self.skill.add_task.assert_called()
        # Assert MQTT command was sent (second add_task call)
        self.assertEqual(self.skill.add_task.call_count, 2)

    async def test_process_request_system_help(self):
        """Test processing SYSTEM_HELP intent request."""
        # Mock template rendering
        mock_template = Mock()
        mock_template.render.return_value = "The CurtainSkill can be used in the following ways..."
        self.skill.intent_to_template[IntentType.SYSTEM_HELP] = mock_template

        # Create intent request
        intent_request = create_mock_intent_request(
            intent_type=IntentType.SYSTEM_HELP,
            current_room="living room",
            text="help with curtains",
        )

        # Process request
        await self.skill.process_request(intent_request)

        # Assert only response was sent (no MQTT command for help)
        self.skill.add_task.assert_called_once()

    async def test_process_request_no_devices_found(self):
        """Test processing intent when no devices are found."""
        # Empty global_devices
        self.skill.global_devices = []

        # Mock send_response
        self.skill.send_response = AsyncMock()

        # Create intent request
        intent_request = create_mock_intent_request(
            intent_type=IntentType.DEVICE_OPEN,
            current_room="living room",
        )

        # Process request
        await self.skill.process_request(intent_request)

        # Assert error response was sent
        self.skill.send_response.assert_called_once()
        call_args = self.skill.send_response.call_args[0]
        self.assertIn("couldn't find any curtains", call_args[0])

    async def test_process_request_set_without_position(self):
        """Test processing DEVICE_SET intent without position number."""
        # Create mock device
        device = create_mock_global_device(room_name="living room")
        self.skill.global_devices = [device]

        # Mock send_response
        self.skill.send_response = AsyncMock()

        # Create intent request without number entity
        intent_request = create_mock_intent_request(
            intent_type=IntentType.DEVICE_SET,
            current_room="living room",
            text="set the curtains",
        )

        # Process request
        await self.skill.process_request(intent_request)

        # Assert clarification request was sent
        self.skill.send_response.assert_called_once()
        call_args = self.skill.send_response.call_args[0]
        self.assertIn("What position", call_args[0])
