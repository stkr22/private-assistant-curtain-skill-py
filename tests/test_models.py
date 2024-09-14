import pytest
from pydantic import ValidationError

from private_assistant_curtain_skill.models import (
    CurtainSkillDevice,  # Assuming the CurtainSkillDevice model is in models.py
)

# Define test cases with valid and invalid topics
valid_topics = [
    "zigbee2mqtt/livingroom/curtain/main",
    "home/automation/curtain/bedroom",
    "devices/kitchen/curtain",
]

invalid_topics = [
    "zigbee2mqtt/livingroom/curtain/main\n",  # Contains newline
    "home/automation/#",  # Contains invalid wildcard
    " devices/kitchen/curtain ",  # Contains leading/trailing whitespace
    "invalid\0topic",  # Contains null character
    "home_home/automation/sensor_sensor/very_long_curtain/very_long_topic_exceeding_maximum_length_beyond_128_characters_to_trigger_error",  # Exceeds max length
]


# Test that valid topics are accepted
@pytest.mark.parametrize("topic", valid_topics)
def test_valid_topics(topic):
    try:
        device = CurtainSkillDevice(topic=topic, alias="Valid Curtain", room="Room")
        assert device.topic == topic.strip()  # Ensure the topic is properly accepted and trimmed
    except ValidationError:
        pytest.fail(f"Valid topic '{topic}' was unexpectedly rejected.")


# Test that invalid topics are rejected
@pytest.mark.parametrize("topic", invalid_topics)
def test_invalid_topics(topic):
    with pytest.raises(ValidationError):
        CurtainSkillDevice(topic=topic, alias="Invalid Curtain", room="Room")
