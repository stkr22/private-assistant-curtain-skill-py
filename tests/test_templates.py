import jinja2
import pytest
from private_assistant_commons import IntentType

from private_assistant_curtain_skill.curtain_skill import Parameters
from private_assistant_curtain_skill.models import CurtainSkillDevice


# Fixture to set up the Jinja2 environment
@pytest.fixture(scope="module")
def jinja_env():
    return jinja2.Environment(
        loader=jinja2.PackageLoader(
            "private_assistant_curtain_skill",
            "templates",
        ),
    )


def render_template(template_name, parameters, env, intent_type=None):
    template = env.get_template(template_name)
    return template.render(parameters=parameters, intent_type=intent_type)


# Test for state.j2 (open/close curtain)
@pytest.mark.parametrize(
    "intent_type, targets, rooms, expected_output",
    [
        (
            IntentType.DEVICE_OPEN,
            [
                CurtainSkillDevice(
                    topic="livingroom/curtain/main",
                    alias="Living Room Curtain",
                    room="Living Room",
                )
            ],
            ["Living Room"],
            "The curtains in the room Living Room have been opened.\n",
        ),
        (
            IntentType.DEVICE_CLOSE,
            [
                CurtainSkillDevice(
                    topic="bedroom/curtain/main",
                    alias="Bedroom Curtain",
                    room="Bedroom",
                )
            ],
            ["Bedroom"],
            "The curtains in the room Bedroom have been closed.\n",
        ),
        (
            IntentType.DEVICE_CLOSE,
            [
                CurtainSkillDevice(
                    topic="bedroom/curtain/main",
                    alias="Bedroom Curtain",
                    room="Bedroom",
                )
            ],
            ["Bedroom", "Living Room"],
            "The curtains in the rooms Bedroom and Living Room have been closed.\n",
        ),
        (IntentType.DEVICE_OPEN, [], ["Bedroom"], "No curtains were found for the specified room.\n"),
    ],
)
def test_state_template(jinja_env, intent_type, targets, rooms, expected_output):
    parameters = Parameters(targets=targets, rooms=rooms)
    result = render_template("state.j2", parameters, jinja_env, intent_type=intent_type)
    assert result == expected_output


# Test for set_curtain.j2 (set curtain position)
@pytest.mark.parametrize(
    "targets, position, rooms, expected_output",
    [
        (
            [
                CurtainSkillDevice(
                    topic="livingroom/curtain/main",
                    alias="Living Room Curtain",
                    room="Living Room",
                )
            ],
            50,
            ["Living Room"],
            "The curtains in the room Living Room have been set to 50%.",
        ),
        (
            [
                CurtainSkillDevice(
                    topic="bedroom/curtain/main",
                    alias="Bedroom Curtain",
                    room="Bedroom",
                )
            ],
            75,
            ["Bedroom", "Living Room"],
            "The curtains in the rooms Bedroom and Living Room have been set to 75%.",
        ),
    ],
)
def test_set_curtain_template(jinja_env, targets, position, rooms, expected_output):
    parameters = Parameters(targets=targets, position=position, rooms=rooms)
    result = render_template("set_curtain.j2", parameters, jinja_env)
    assert result == expected_output
