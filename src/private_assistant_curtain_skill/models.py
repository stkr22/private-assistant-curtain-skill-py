import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, field_validator

if TYPE_CHECKING:
    from private_assistant_commons.database.models import GlobalDevice

# Define a regex pattern for a valid MQTT topic
MQTT_TOPIC_REGEX = re.compile(r"[\$#\+\s\0-\31]+")
MAX_TOPIC_LENGTH = 128


class CurtainSkillDevice(BaseModel):
    """
    Pydantic model representing a curtain device with MQTT control configuration.

    This model is used to extract curtain-specific data from the global device registry.
    The actual device data is stored in GlobalDevice.device_attributes.
    """

    topic: str
    alias: str
    room: str
    payload_open: str = '{"state": "OPEN"}'
    payload_close: str = '{"state": "CLOSE"}'
    payload_set_template: str = '{"position": {{ position }}}'

    # Validate the topic field to ensure it conforms to MQTT standards
    @field_validator("topic")
    @classmethod
    def validate_topic(cls, value: str) -> str:
        """Validate MQTT topic format."""
        # Check for any invalid characters in the topic
        if MQTT_TOPIC_REGEX.findall(value):
            raise ValueError("Topic must not contain '+', '#', whitespace, or control characters.")
        if len(value) > MAX_TOPIC_LENGTH:
            raise ValueError(f"Topic length exceeds maximum allowed limit ({MAX_TOPIC_LENGTH} characters).")

        return value.strip()

    @classmethod
    def from_global_device(cls, global_device: "GlobalDevice") -> "CurtainSkillDevice":
        """
        Transform GlobalDevice to CurtainSkillDevice with type safety.

        Args:
            global_device: The global device registry entry

        Returns:
            CurtainSkillDevice with MQTT configuration extracted from device_attributes
        """
        attrs = global_device.device_attributes or {}
        return cls(
            topic=attrs.get("topic", ""),
            alias=global_device.name,
            room=global_device.room.name if global_device.room else "",
            payload_open=attrs.get("payload_open", '{"state": "OPEN"}'),
            payload_close=attrs.get("payload_close", '{"state": "CLOSE"}'),
            payload_set_template=attrs.get("payload_set_template", '{"position": {{ position }}}'),
        )
