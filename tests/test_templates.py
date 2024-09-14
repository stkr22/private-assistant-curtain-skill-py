import jinja2
import pytest

from private_assistant_curtain_skill.curtain_skill import Action, Parameters
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


def render_template(template_name, parameters, env, action=None):
    template = env.get_template(template_name)
    return template.render(parameters=parameters, action=action)


# Test for help.j2
@pytest.mark.parametrize(
    "expected_output",
    [
        (
            "Here is how you can use the CurtainSkill:\n"
            "- Say 'open the curtain' to open a device.\n"
            "- Say 'close the curtain' to close a device.\n"
            "- Say 'set curtain to 50' to set the curtain to 50%."
        ),
    ],
)
def test_help_template(jinja_env, expected_output):
    result = render_template("help.j2", Parameters(), jinja_env)
    assert result == expected_output


# Test for state.j2 (open/close curtain)
@pytest.mark.parametrize(
    "action, targets, expected_output",
    [
        (Action.OPEN, [CurtainSkillDevice(alias="Living Room Curtain")], "I have opened the curtains.\n"),
        (Action.CLOSE, [CurtainSkillDevice(alias="Bedroom Curtain")], "I have closed the curtains.\n"),
        (Action.OPEN, [], "No curtains found for this room.\n"),
    ],
)
def test_state_template(jinja_env, action, targets, expected_output):
    parameters = Parameters(targets=targets)
    result = render_template("state.j2", parameters, jinja_env, action=action)
    assert result == expected_output


# Test for set_curtain.j2 (set curtain position)
@pytest.mark.parametrize(
    "targets, position, expected_output",
    [
        (
            [CurtainSkillDevice(alias="Living Room Curtain")],
            50,
            "I have set the curtains to 50%.",
        ),
        (
            [CurtainSkillDevice(alias="Bedroom Curtain")],
            75,
            "I have set the curtains to 75%.",
        ),
    ],
)
def test_set_curtain_template(jinja_env, targets, position, expected_output):
    parameters = Parameters(targets=targets, position=position)
    result = render_template("set_curtain.j2", parameters, jinja_env)
    assert result == expected_output
