import asyncio
from enum import Enum

import aiomqtt
import jinja2
import private_assistant_commons as commons
from private_assistant_commons.skill_logger import SkillLogger
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from private_assistant_curtain_skill.models import CurtainSkillDevice

logger = SkillLogger.get_logger(__name__)


class Parameters(BaseModel):
    position: int = 0
    targets: list[CurtainSkillDevice] = []


class Action(Enum):
    HELP = "help"
    OPEN = "open"
    CLOSE = "close"
    SET = "set"

    @classmethod
    def find_matching_action(cls, verbs: list):
        for action in cls:
            if action.value in verbs:
                return action
        return None


class CurtainSkill(commons.BaseSkill):
    def __init__(
        self,
        config_obj: commons.SkillConfig,
        mqtt_client: aiomqtt.Client,
        db_engine: AsyncEngine,
        template_env: jinja2.Environment,
        task_group: asyncio.TaskGroup,
        logger,
    ) -> None:
        super().__init__(config_obj, mqtt_client, task_group, logger=logger)
        self.db_engine = db_engine
        self.template_env = template_env
        self._device_cache: dict[str, list[CurtainSkillDevice]] = {}
        self.action_to_answer: dict[Action, jinja2.Template] = {}

        # Preload templates
        try:
            self.action_to_answer[Action.HELP] = self.template_env.get_template("help.j2")
            self.action_to_answer[Action.OPEN] = self.template_env.get_template("state.j2")
            self.action_to_answer[Action.CLOSE] = self.template_env.get_template("state.j2")
            self.action_to_answer[Action.SET] = self.template_env.get_template("set_curtain.j2")
            self.logger.debug("Templates successfully loaded during initialization.")
        except jinja2.TemplateNotFound as e:
            self.logger.error("Failed to load template: %s", e)

    async def load_device_cache(self) -> None:
        """Asynchronously load devices into the cache."""
        if not self._device_cache:
            self.logger.debug("Loading devices into cache asynchronously.")
            async with AsyncSession(self.db_engine) as session:
                statement = select(CurtainSkillDevice)
                result = await session.exec(statement)
                devices = result.all()
                for device in devices:
                    try:
                        device.model_validate(device)
                        if device.room not in self._device_cache:
                            self._device_cache[device.room] = []
                        self._device_cache[device.room].append(device)
                    except ValidationError as e:
                        self.logger.error("Validation error loading device into cache: %s", e)

    async def get_devices(self, room: str) -> list[CurtainSkillDevice]:
        """Return devices for a specific room, using async cache loading."""
        if not self._device_cache:
            await self.load_device_cache()
        self.logger.info("Fetching devices for room: %s", room)
        return self._device_cache.get(room, [])

    async def calculate_certainty(self, intent_analysis_result: commons.IntentAnalysisResult) -> float:
        if "curtain" in intent_analysis_result.nouns or "curtains" in intent_analysis_result.nouns:
            self.logger.debug("Curtain noun detected, certainty set to 1.0.")
            return 1.0
        self.logger.debug("No curtain noun detected, certainty set to 0.")
        return 0

    async def find_parameters(self, action: Action, intent_analysis_result: commons.IntentAnalysisResult) -> Parameters:
        parameters = Parameters()
        devices = await self.get_devices(intent_analysis_result.client_request.room)
        if action in [Action.OPEN, Action.CLOSE, Action.SET]:
            parameters.targets = [device for device in devices]
        if action == Action.SET and intent_analysis_result.numbers:
            parameters.position = intent_analysis_result.numbers[0].number_token
        self.logger.debug("Parameters found for action %s: %s", action, parameters)
        return parameters

    def get_answer(self, action: Action, parameters: Parameters) -> str:
        template = self.action_to_answer.get(action)
        if template:
            answer = template.render(
                action=action,
                parameters=parameters,
            )
            self.logger.debug("Generated answer using template for action %s.", action)
            return answer
        else:
            self.logger.error("No template found for action %s.", action)
            return "Sorry, I couldn't process your request."

    async def send_mqtt_command(self, action: Action, parameters: Parameters) -> None:
        """Send the MQTT command asynchronously."""
        for device in parameters.targets:
            if action == Action.OPEN:
                payload = device.payload_open
            elif action == Action.CLOSE:
                payload = device.payload_close
            elif action == Action.SET:
                payload = jinja2.Template(device.payload_set_template).render(position=parameters.position)
            else:
                self.logger.error("Unknown action: %s", action)
                continue

            self.logger.info("Sending payload %s to topic %s via MQTT.", payload, device.topic)
            try:
                await self.mqtt_client.publish(device.topic, payload, qos=1)
            except Exception as e:
                self.logger.error("Failed to send MQTT message to topic %s: %s", device.topic, e, exc_info=True)

    async def process_request(self, intent_analysis_result: commons.IntentAnalysisResult) -> None:
        action = Action.find_matching_action(intent_analysis_result.verbs)
        if action is None:
            self.logger.error("Unrecognized action in verbs: %s", intent_analysis_result.verbs)
            return

        parameters = await self.find_parameters(action, intent_analysis_result)
        if parameters.targets:
            answer = self.get_answer(action, parameters)
            self.add_task(self.add_text_to_output_topic(answer, client_request=intent_analysis_result.client_request))
            if action not in [Action.HELP]:
                self.add_task(self.send_mqtt_command(action, parameters))
        else:
            self.logger.error("No targets found for action %s.", action)
