"""End-to-end integration tests for the curtain skill.

These tests validate the complete skill workflow with real external services:
- PostgreSQL database (device registry)
- MQTT broker (message bus)
- Curtain skill running in background

Test flow:
1. Setup database with test devices
2. Start skill in background
3. Publish IntentRequest to MQTT
4. Assert skill publishes correct device commands and responses

Run these tests with:
    pytest tests/test_integration.py -v -m integration -n 0

Requirements:
- Compose services (PostgreSQL, Mosquitto) must be running
"""

import asyncio
import contextlib
import json
import logging
import os
import pathlib
import tempfile
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import cast

import aiomqtt
import pytest
import yaml
from private_assistant_commons import ClassifiedIntent, ClientRequest, Entity, EntityType, IntentRequest, IntentType
from private_assistant_commons.database import DeviceType, GlobalDevice, Room, Skill, create_skill_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from private_assistant_curtain_skill.main import start_skill

# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration

# Logger for test debugging
logger = logging.getLogger(__name__)


@pytest.fixture(scope="function")
async def db_engine():
    """Create a database engine for integration tests."""
    engine = create_skill_engine()

    # Ensure tables exist
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)

    await engine.dispose()


@pytest.fixture
async def db_session(db_engine):
    """Create a database session for each test."""
    async with AsyncSession(db_engine) as session:
        yield session


@pytest.fixture
def mqtt_config():
    """Get MQTT configuration from environment variables."""
    return {
        "host": os.getenv("MQTT_HOST", "mosquitto"),
        "port": int(os.getenv("MQTT_PORT", "1883")),
    }


@pytest.fixture
async def mqtt_test_client(mqtt_config):
    """Create an MQTT test client."""
    async with aiomqtt.Client(hostname=mqtt_config["host"], port=mqtt_config["port"]) as client:
        yield client


@pytest.fixture
async def test_skill_entity(db_session) -> Skill:
    """Create a test skill entity in the database."""
    result = await db_session.exec(select(Skill).where(Skill.name == "curtain-skill-integration-test"))
    skill = result.first()

    if skill is None:
        skill = Skill(name="curtain-skill-integration-test")
        db_session.add(skill)
        await db_session.flush()
        await db_session.refresh(skill)

    assert skill is not None
    return cast("Skill", skill)


@pytest.fixture
async def test_device_type(db_session) -> DeviceType:
    """Create a test device type in the database."""
    result = await db_session.exec(select(DeviceType).where(DeviceType.name == "curtain"))
    device_type = result.first()

    if device_type is None:
        device_type = DeviceType(name="curtain")
        db_session.add(device_type)
        await db_session.flush()
        await db_session.refresh(device_type)

    assert device_type is not None
    return cast("DeviceType", device_type)


@pytest.fixture
async def test_room(db_session) -> Room:
    """Create a test room in the database (studio).

    Uses realistic room naming patterns based on typical smart home setups.
    """
    room = Room(name="studio")
    db_session.add(room)
    await db_session.flush()
    await db_session.refresh(room)
    return room


@pytest.fixture
async def test_room_office(db_session) -> Room:
    """Create a second test room (office) for multi-room tests."""
    room = Room(name="office")
    db_session.add(room)
    await db_session.flush()
    await db_session.refresh(room)
    return room


@pytest.fixture
async def test_room_guest(db_session) -> Room:
    """Create a third test room (guest room) for multi-room tests."""
    room = Room(name="guest room")
    db_session.add(room)
    await db_session.flush()
    await db_session.refresh(room)
    return room


@pytest.fixture
async def test_device(db_session, test_skill_entity, test_device_type, test_room) -> AsyncGenerator[GlobalDevice, None]:
    """Create a single test device in the database (studio window blinds).

    Uses realistic MQTT topic patterns based on typical zigbee2mqtt setups.
    Note: This fixture must be created BEFORE the running_skill fixture
    so the device is loaded during skill initialization.
    """
    await db_session.refresh(test_room)
    await db_session.refresh(test_skill_entity)
    await db_session.refresh(test_device_type)

    logger.debug("Creating device with skill_id=%s, skill_name=%s", test_skill_entity.id, test_skill_entity.name)

    device = GlobalDevice(
        device_type_id=test_device_type.id,
        name="window blinds",
        pattern=["window blinds", "blinds"],
        device_attributes={
            "topic": "zigbee2mqtt/studio/motor/window_blinds/set",
            "payload_open": '{"state": "OPEN"}',
            "payload_close": '{"state": "CLOSE"}',
            "payload_set_template": '{"position": {{ position }}}',
        },
        room_id=test_room.id,
        skill_id=test_skill_entity.id,
    )
    db_session.add(device)
    await db_session.commit()
    await db_session.refresh(device, ["room"])

    logger.debug("Device created with ID=%s, skill_id=%s", device.id, device.skill_id)

    yield device

    # Cleanup: Delete test device
    logger.debug("Cleaning up device %s", device.id)
    await db_session.delete(device)
    await db_session.commit()


@pytest.fixture
async def test_device_office(
    db_session, test_skill_entity, test_device_type, test_room_office
) -> AsyncGenerator[GlobalDevice, None]:
    """Create an office curtain device for multi-room tests."""
    await db_session.refresh(test_room_office)
    await db_session.refresh(test_skill_entity)
    await db_session.refresh(test_device_type)

    device = GlobalDevice(
        device_type_id=test_device_type.id,
        name="door curtain",
        pattern=["door curtain", "curtain"],
        device_attributes={
            "topic": "zigbee2mqtt/office/motor/door_curtain/set",
            "payload_open": '{"state": "OPEN"}',
            "payload_close": '{"state": "CLOSE"}',
            "payload_set_template": '{"position": {{ position }}}',
        },
        room_id=test_room_office.id,
        skill_id=test_skill_entity.id,
    )
    db_session.add(device)
    await db_session.commit()
    await db_session.refresh(device, ["room"])

    yield device

    await db_session.delete(device)
    await db_session.commit()


@pytest.fixture
async def skill_config_file(mqtt_config):  # noqa: ARG001
    """Create a temporary config file for the skill."""
    config = {
        "client_id": "curtain-skill-integration-test",
        "base_topic": "assistant",
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(config, f)
        config_path = pathlib.Path(f.name)

    yield config_path

    # Cleanup: Remove temp file
    config_path.unlink(missing_ok=True)


@pytest.fixture
async def running_skill(skill_config_file, test_device, db_engine):  # noqa: ARG001
    """Start the skill in background with a test device ready.

    Args:
        skill_config_file: Path to skill config
        test_device: Test device that must be created before skill starts
        db_engine: Database engine (unused but ensures order)
    """
    # Device is already created by test_device fixture
    # Give database time to fully persist the commit
    await asyncio.sleep(0.5)

    # Start skill as background task
    skill_task = asyncio.create_task(start_skill(skill_config_file))

    # Wait for skill to initialize and subscribe to all topics
    await asyncio.sleep(3)

    # Trigger device load by publishing device update notification
    mqtt_host = os.getenv("MQTT_HOST", "mosquitto")
    mqtt_port = int(os.getenv("MQTT_PORT", "1883"))
    async with aiomqtt.Client(hostname=mqtt_host, port=mqtt_port) as trigger_client:
        await trigger_client.publish("assistant/global_device_update", "", qos=1)

    # Wait for skill to process the device update and load devices
    await asyncio.sleep(2)

    yield

    # Cleanup: Cancel skill task
    skill_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await skill_task


@pytest.fixture
async def running_skill_no_devices(skill_config_file, db_engine):  # noqa: ARG001
    """Start the skill in background without any test devices.

    Used for tests that don't need devices (e.g., error handling tests).
    Depends on db_engine to ensure database tables are created.
    """
    # Start skill as background task
    skill_task = asyncio.create_task(start_skill(skill_config_file))

    # Wait for skill to initialize and subscribe to topics
    await asyncio.sleep(3)

    yield

    # Cleanup: Cancel skill task
    skill_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await skill_task


class TestDeviceOpenCommand:
    """Test curtain open commands (DEVICE_OPEN)."""

    async def test_open_curtain_command(
        self,
        test_device,
        test_room,
        running_skill,  # noqa: ARG002
        mqtt_test_client,
    ):
        """Test that DEVICE_OPEN intent triggers correct MQTT command and response.

        Uses realistic device setup:
        - Room: studio
        - Device: window blinds
        - MQTT: zigbee2mqtt/studio/motor/window_blinds/set

        Flow:
        1. Publish IntentRequest with DEVICE_OPEN intent
        2. Assert device command published to correct topic with correct payload
        3. Assert response published to output topic
        """
        output_topic = f"assistant/studio/output/{uuid.uuid4().hex[:8]}"
        device_topic = test_device.device_attributes["topic"]

        # Subscribe to topics before sending request
        await mqtt_test_client.subscribe(output_topic)
        await mqtt_test_client.subscribe(device_topic)

        # Build IntentRequest
        classified_intent = ClassifiedIntent(
            id=uuid.uuid4(),
            intent_type=IntentType.DEVICE_OPEN,
            confidence=0.95,
            entities={
                "device": [
                    Entity(
                        id=uuid.uuid4(),
                        type=EntityType.DEVICE,
                        raw_text="window blinds",
                        normalized_value="curtain",
                        confidence=0.95,
                        metadata={"device_type": "curtain", "is_generic": False},
                        linked_to=[],
                    )
                ]
            },
            alternative_intents=[],
            raw_text="open the window blinds",
            timestamp=datetime.now(),
        )

        client_request = ClientRequest(
            id=uuid.uuid4(),
            text="open the window blinds",
            room=test_room.name,
            output_topic=output_topic,
        )

        intent_request = IntentRequest(
            id=uuid.uuid4(),
            classified_intent=classified_intent,
            client_request=client_request,
        )

        # Publish IntentRequest to skill's input topic
        intent_json = intent_request.model_dump_json()
        await mqtt_test_client.publish("assistant/intent_engine/result", intent_json, qos=1)

        # Collect messages
        device_command_received = False
        response_received = False
        timeout_seconds = 5

        try:
            async with asyncio.timeout(timeout_seconds):
                async for message in mqtt_test_client.messages:
                    topic = str(message.topic)
                    payload = message.payload.decode()

                    if topic == device_topic:
                        command = json.loads(payload)
                        assert command["state"] == "OPEN"
                        device_command_received = True
                        logger.debug("Device command received: %s", command)

                    elif topic == output_topic:
                        assert "opened" in payload.lower()
                        response_received = True
                        logger.debug("Response received: %s", payload)

                    if device_command_received and response_received:
                        break
        except TimeoutError:
            pytest.fail(f"Timeout waiting for messages after {timeout_seconds}s")

        assert device_command_received, "Device command was not received"
        assert response_received, "Response was not received"


class TestDeviceCloseCommand:
    """Test curtain close commands (DEVICE_CLOSE)."""

    async def test_close_curtain_command(
        self,
        test_device,
        test_room,
        running_skill,  # noqa: ARG002
        mqtt_test_client,
    ):
        """Test that DEVICE_CLOSE intent triggers correct MQTT command.

        Uses realistic device setup:
        - Room: studio
        - Device: window blinds
        - MQTT: zigbee2mqtt/studio/motor/window_blinds/set
        """
        output_topic = f"assistant/studio/output/{uuid.uuid4().hex[:8]}"
        device_topic = test_device.device_attributes["topic"]

        await mqtt_test_client.subscribe(output_topic)
        await mqtt_test_client.subscribe(device_topic)

        classified_intent = ClassifiedIntent(
            id=uuid.uuid4(),
            intent_type=IntentType.DEVICE_CLOSE,
            confidence=0.95,
            entities={
                "device": [
                    Entity(
                        id=uuid.uuid4(),
                        type=EntityType.DEVICE,
                        raw_text="blinds",
                        normalized_value="curtain",
                        confidence=0.95,
                        metadata={"device_type": "curtain", "is_generic": False},
                        linked_to=[],
                    )
                ]
            },
            alternative_intents=[],
            raw_text="close the blinds",
            timestamp=datetime.now(),
        )

        client_request = ClientRequest(
            id=uuid.uuid4(),
            text="close the blinds",
            room=test_room.name,
            output_topic=output_topic,
        )

        intent_request = IntentRequest(
            id=uuid.uuid4(),
            classified_intent=classified_intent,
            client_request=client_request,
        )

        intent_json = intent_request.model_dump_json()
        await mqtt_test_client.publish("assistant/intent_engine/result", intent_json, qos=1)

        device_command_received = False
        timeout_seconds = 5

        try:
            async with asyncio.timeout(timeout_seconds):
                async for message in mqtt_test_client.messages:
                    if str(message.topic) == device_topic:
                        command = json.loads(message.payload.decode())
                        assert command["state"] == "CLOSE"
                        device_command_received = True
                        break
        except TimeoutError:
            pytest.fail(f"Timeout waiting for device command after {timeout_seconds}s")

        assert device_command_received, "Device command was not received"


class TestDeviceSetCommand:
    """Test curtain position set commands (DEVICE_SET)."""

    async def test_set_curtain_position(
        self,
        test_device,
        test_room,
        running_skill,  # noqa: ARG002
        mqtt_test_client,
    ):
        """Test that DEVICE_SET intent with position triggers correct MQTT command.

        Uses realistic device setup and entity structure:
        - Room: studio
        - Device: window blinds
        - MQTT: zigbee2mqtt/studio/motor/window_blinds/set
        - Entity: singular "number" key (matching intent classifier format)
        """
        output_topic = f"assistant/studio/output/{uuid.uuid4().hex[:8]}"
        device_topic = test_device.device_attributes["topic"]
        target_position = 75

        await mqtt_test_client.subscribe(device_topic)

        classified_intent = ClassifiedIntent(
            id=uuid.uuid4(),
            intent_type=IntentType.DEVICE_SET,
            confidence=0.95,
            entities={
                "device": [
                    Entity(
                        id=uuid.uuid4(),
                        type=EntityType.DEVICE,
                        raw_text="blinds",
                        normalized_value="curtain",
                        confidence=0.95,
                        metadata={"device_type": "curtain", "is_generic": False},
                        linked_to=[],
                    )
                ],
                "number": [
                    Entity(
                        id=uuid.uuid4(),
                        type=EntityType.NUMBER,
                        raw_text="75",
                        normalized_value=target_position,
                        confidence=1.0,
                        metadata={},
                        linked_to=[],
                    )
                ],
            },
            alternative_intents=[],
            raw_text="set blinds to 75 percent",
            timestamp=datetime.now(),
        )

        client_request = ClientRequest(
            id=uuid.uuid4(),
            text="set blinds to 75 percent",
            room=test_room.name,
            output_topic=output_topic,
        )

        intent_request = IntentRequest(
            id=uuid.uuid4(),
            classified_intent=classified_intent,
            client_request=client_request,
        )

        intent_json = intent_request.model_dump_json()
        await mqtt_test_client.publish("assistant/intent_engine/result", intent_json, qos=1)

        device_command_received = False
        timeout_seconds = 5

        try:
            async with asyncio.timeout(timeout_seconds):
                async for message in mqtt_test_client.messages:
                    if str(message.topic) == device_topic:
                        command = json.loads(message.payload.decode())
                        assert command["position"] == target_position
                        device_command_received = True
                        break
        except TimeoutError:
            pytest.fail(f"Timeout waiting for device command after {timeout_seconds}s")

        assert device_command_received, "Device command was not received"


class TestDeviceNotFound:
    """Test error handling when devices are not found."""

    async def test_device_not_found_in_room(
        self,
        running_skill_no_devices,  # noqa: ARG002
        mqtt_test_client,
    ):
        """Test handling when no devices are found in specified room.

        Uses realistic room name that doesn't have any devices configured.
        """
        output_topic = f"assistant/garage/output/{uuid.uuid4().hex[:8]}"

        await mqtt_test_client.subscribe(output_topic)

        classified_intent = ClassifiedIntent(
            id=uuid.uuid4(),
            intent_type=IntentType.DEVICE_OPEN,
            confidence=0.95,
            entities={
                "device": [
                    Entity(
                        id=uuid.uuid4(),
                        type=EntityType.DEVICE,
                        raw_text="curtain",
                        normalized_value="curtain",
                        confidence=0.95,
                        metadata={"device_type": "curtain", "is_generic": True},
                        linked_to=[],
                    )
                ]
            },
            alternative_intents=[],
            raw_text="open the curtain",
            timestamp=datetime.now(),
        )

        client_request = ClientRequest(
            id=uuid.uuid4(),
            text="open the curtain",
            room="garage",
            output_topic=output_topic,
        )

        intent_request = IntentRequest(
            id=uuid.uuid4(),
            classified_intent=classified_intent,
            client_request=client_request,
        )

        intent_json = intent_request.model_dump_json()
        await mqtt_test_client.publish("assistant/intent_engine/result", intent_json, qos=1)

        response_received = False
        timeout_seconds = 5

        try:
            async with asyncio.timeout(timeout_seconds):
                async for message in mqtt_test_client.messages:
                    if str(message.topic) == output_topic:
                        payload = message.payload.decode()
                        assert "couldn't find" in payload.lower() or "no curtains" in payload.lower()
                        response_received = True
                        break
        except TimeoutError:
            pytest.fail(f"Timeout waiting for error response after {timeout_seconds}s")

        assert response_received, "Error response was not received"
