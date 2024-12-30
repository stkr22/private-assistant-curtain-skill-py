import re

from pydantic import field_validator
from sqlmodel import Field, SQLModel

# Define a regex pattern for a valid MQTT topic
MQTT_TOPIC_REGEX = re.compile(r"[\$#\+\s\0-\31]+")


class SQLModelValidation(SQLModel):
    """
    Helper class to allow for validation in SQLModel classes with table=True
    """

    model_config = {"from_attributes": True, "validate_assignment": True}


class CurtainSkillDevice(SQLModelValidation, table=True):  # type: ignore
    id: int | None = Field(default=None, primary_key=True)
    topic: str
    alias: str
    room: str
    payload_open: str = '{"state": "OPEN"}'  # Standardized payload for opening the curtain
    payload_close: str = '{"state": "CLOSE"}'  # Standardized payload for closing the curtain
    payload_set_template: str = '{"position": {{ position }}}'  # Standardized template for setting position

    # Validate the topic field to ensure it conforms to MQTT standards
    @field_validator("topic")
    @classmethod
    def validate_topic(cls, value: str):
        # Check for any invalid characters in the topic
        if MQTT_TOPIC_REGEX.findall(value):
            raise ValueError("Topic must not contain '+', '#', whitespace, or control characters.")
        if len(value) > 128:
            raise ValueError("Topic length exceeds maximum allowed limit (128 characters).")

        # Trim any leading or trailing whitespace just in case
        return value.strip()
