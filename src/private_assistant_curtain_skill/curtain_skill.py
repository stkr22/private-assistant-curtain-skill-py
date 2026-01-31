"""Curtain skill for controlling smart curtains via MQTT commands."""

import asyncio
import logging

import aiomqtt
import jinja2
import private_assistant_commons as commons
from private_assistant_commons import (
    IntentRequest,
    IntentType,
)
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine

from private_assistant_curtain_skill.models import CurtainSkillDevice


class Parameters(BaseModel):
    """Parameters for executing curtain commands."""

    position: int = 0
    targets: list[CurtainSkillDevice] = []
    rooms: list[str] = []


class CurtainSkill(commons.BaseSkill):
    """Skill for controlling curtains via MQTT commands.

    Supports opening, closing, and setting curtain positions through voice commands
    or text-based intent requests.
    """

    help_text = (
        "The CurtainSkill can be used in the following ways:\n"
        '- "Open the curtain" to open a curtain.\n'
        '- "Close the curtain" to close a curtain.\n'
        '- "Set curtain to 50" to set the curtain to 50%.'
    )

    def __init__(  # noqa: PLR0913
        self,
        config_obj: commons.SkillConfig,
        mqtt_client: aiomqtt.Client,
        template_env: jinja2.Environment,
        task_group: asyncio.TaskGroup,
        engine: AsyncEngine,
        logger: logging.Logger,
    ) -> None:
        """Initialize the CurtainSkill with required dependencies.

        Args:
            config_obj: Skill configuration
            mqtt_client: MQTT client for device communication
            template_env: Jinja2 environment for response templates
            task_group: Asyncio task group for background tasks
            engine: SQLAlchemy async engine for database operations
            logger: Logger instance for this skill

        """
        # Pass engine to BaseSkill (NEW REQUIRED PARAMETER)
        super().__init__(
            config_obj=config_obj,
            mqtt_client=mqtt_client,
            task_group=task_group,
            engine=engine,
            logger=logger,
        )

        # Configure supported intents (replaces calculate_certainty)
        self.supported_intents = {
            IntentType.DEVICE_OPEN: 0.8,  # "open the curtains"
            IntentType.DEVICE_CLOSE: 0.8,  # "close the curtains"
            IntentType.DEVICE_SET: 0.8,  # "set curtain to 50%"
        }

        # Configure device types for device registry
        self.supported_device_types = ["curtain"]

        # Store template environment for response generation
        self.template_env = template_env

        # Template mapping for intent types
        self.intent_to_template: dict[IntentType, jinja2.Template] = {}

        # Load all templates
        self._load_templates()

    def _load_templates(self) -> None:
        """Load and validate all required templates with fallback handling.

        Raises:
            RuntimeError: If critical templates cannot be loaded

        """
        template_mappings = {
            IntentType.DEVICE_OPEN: "state.j2",
            IntentType.DEVICE_CLOSE: "state.j2",
            IntentType.DEVICE_SET: "set_curtain.j2",
        }

        failed_templates = []
        for intent_type, template_name in template_mappings.items():
            try:
                self.intent_to_template[intent_type] = self.template_env.get_template(template_name)
            except jinja2.TemplateNotFound as e:
                self.logger.error("Failed to load template %s: %s", template_name, e)
                failed_templates.append(template_name)

        if failed_templates:
            raise RuntimeError(f"Critical templates failed to load: {', '.join(failed_templates)}")

        self.logger.debug("All templates successfully loaded during initialization.")

    def _is_curtain_intent(self, classified_intent: commons.ClassifiedIntent) -> bool:
        """Validate if the intent is actually for curtain control.

        Checks for device entity with:
        1. device_type in supported_device_types OR
        2. is_generic=True with normalized_value in supported_device_types

        Args:
            classified_intent: The classified intent with extracted entities

        Returns:
            True if intent is for curtain control, False otherwise

        """
        # Check for device entities
        device_entities = classified_intent.entities.get("device", [])
        for device_entity in device_entities:
            device_type = device_entity.metadata.get("device_type", "")
            is_generic = device_entity.metadata.get("is_generic", False)

            # Accept if device_type is in supported_device_types OR it's a generic device reference
            if device_type in self.supported_device_types or (
                is_generic and device_entity.normalized_value in self.supported_device_types
            ):
                self.logger.debug("Found curtain device entity: %s", device_entity.normalized_value)
                return True

        # No curtain-related entities found
        self.logger.debug("No curtain-related entities found in intent")
        return False

    def _get_curtain_devices(self, target_rooms: list[str]) -> list[CurtainSkillDevice]:
        """Get curtain devices from global registry for specified rooms.

        Args:
            target_rooms: List of room names to filter devices by

        Returns:
            List of CurtainSkillDevice instances for the target rooms

        """
        curtain_devices = []
        for device in self.global_devices:
            # Check if device is a curtain and in one of the target rooms
            if device.device_type.name == "curtain" and device.room and device.room.name in target_rooms:
                try:
                    curtain_device = CurtainSkillDevice.from_global_device(device)
                    curtain_devices.append(curtain_device)
                except Exception as e:
                    self.logger.error(
                        "Failed to transform device %s to CurtainSkillDevice: %s",
                        device.name,
                        e,
                        exc_info=True,
                    )
        return curtain_devices

    async def _extract_parameters(self, intent_request: IntentRequest) -> Parameters:
        """Extract parameters from IntentRequest for curtain control.

        Args:
            intent_request: The validated intent request

        Returns:
            Parameters object with devices, rooms, and position

        """
        classified_intent = intent_request.classified_intent
        client_request = intent_request.client_request

        parameters = Parameters()

        # Extract room entities or default to current room
        room_entities = classified_intent.entities.get("room", [])
        if room_entities:
            parameters.rooms = [entity.normalized_value for entity in room_entities]
        else:
            parameters.rooms = [client_request.room]

        # Get curtain devices for target rooms
        parameters.targets = self._get_curtain_devices(parameters.rooms)

        # Extract position for SET intent
        if classified_intent.intent_type == IntentType.DEVICE_SET:
            number_entities = classified_intent.entities.get("number", [])
            if number_entities:
                try:
                    parameters.position = int(number_entities[0].normalized_value)
                except (ValueError, TypeError) as e:
                    self.logger.warning("Failed to parse position from entity: %s", e)
                    parameters.position = 0

        self.logger.debug(
            "Extracted parameters for intent %s: %d targets in rooms %s",
            classified_intent.intent_type,
            len(parameters.targets),
            parameters.rooms,
        )
        return parameters

    def _render_response(self, intent_type: IntentType, parameters: Parameters) -> str:
        """Render response template for the given intent type.

        Args:
            intent_type: The type of intent to render response for
            parameters: Parameters containing device and room information

        Returns:
            Rendered response string

        """
        template = self.intent_to_template.get(intent_type)
        if template:
            answer = template.render(
                intent_type=intent_type,
                parameters=parameters,
            )
            self.logger.debug("Generated answer using template for intent %s.", intent_type)
            return answer
        self.logger.error("No template found for intent %s.", intent_type)
        return "Sorry, I couldn't process your request."

    async def _send_mqtt_commands(self, intent_type: IntentType, parameters: Parameters) -> None:
        """Send MQTT commands to control curtain devices.

        Args:
            intent_type: The intent type (DEVICE_OPEN, DEVICE_CLOSE, DEVICE_SET)
            parameters: Parameters containing target devices and position

        """
        for device in parameters.targets:
            # Determine payload based on intent type
            if intent_type == IntentType.DEVICE_OPEN:
                payload = device.payload_open
            elif intent_type == IntentType.DEVICE_CLOSE:
                payload = device.payload_close
            elif intent_type == IntentType.DEVICE_SET:
                payload = jinja2.Template(device.payload_set_template).render(position=parameters.position)
            else:
                self.logger.error("Unknown intent type for MQTT command: %s", intent_type)
                continue

            self.logger.info("Sending payload %s to topic %s via MQTT.", payload, device.topic)
            try:
                await self.mqtt_client.publish(device.topic, payload, qos=1)
            except Exception as e:
                self.logger.error("Failed to send MQTT message to topic %s: %s", device.topic, e, exc_info=True)

    async def _handle_device_open(self, intent_request: IntentRequest) -> None:
        """Handle DEVICE_OPEN intent - open curtains.

        Args:
            intent_request: The intent request with classified intent and client request

        """
        client_request = intent_request.client_request

        # Extract parameters from entities
        parameters = await self._extract_parameters(intent_request)

        if not parameters.targets:
            await self.send_response(
                f"I couldn't find any curtains in {', '.join(parameters.rooms)}.",
                client_request=client_request,
            )
            return

        # Send response and MQTT commands
        answer = self._render_response(IntentType.DEVICE_OPEN, parameters)
        self.add_task(self.send_response(answer, client_request=client_request))
        self.add_task(self._send_mqtt_commands(IntentType.DEVICE_OPEN, parameters))

    async def _handle_device_close(self, intent_request: IntentRequest) -> None:
        """Handle DEVICE_CLOSE intent - close curtains.

        Args:
            intent_request: The intent request with classified intent and client request

        """
        client_request = intent_request.client_request

        # Extract parameters from entities
        parameters = await self._extract_parameters(intent_request)

        if not parameters.targets:
            await self.send_response(
                f"I couldn't find any curtains in {', '.join(parameters.rooms)}.",
                client_request=client_request,
            )
            return

        # Send response and MQTT commands
        answer = self._render_response(IntentType.DEVICE_CLOSE, parameters)
        self.add_task(self.send_response(answer, client_request=client_request))
        self.add_task(self._send_mqtt_commands(IntentType.DEVICE_CLOSE, parameters))

    async def _handle_device_set(self, intent_request: IntentRequest) -> None:
        """Handle DEVICE_SET intent - set curtain position.

        Args:
            intent_request: The intent request with classified intent and client request

        """
        classified_intent = intent_request.classified_intent
        client_request = intent_request.client_request

        # Extract parameters from entities
        parameters = await self._extract_parameters(intent_request)

        if not parameters.targets:
            await self.send_response(
                f"I couldn't find any curtains in {', '.join(parameters.rooms)}.",
                client_request=client_request,
            )
            return

        if parameters.position == 0:
            number_entities = classified_intent.entities.get("number", [])
            if not number_entities:
                await self.send_response(
                    "What position would you like to set the curtains to?",
                    client_request=client_request,
                )
                return

        # Send response and MQTT commands
        answer = self._render_response(IntentType.DEVICE_SET, parameters)
        self.add_task(self.send_response(answer, client_request=client_request))
        self.add_task(self._send_mqtt_commands(IntentType.DEVICE_SET, parameters))

    async def process_request(self, intent_request: IntentRequest) -> None:
        """Process intent request and route to appropriate handler.

        Orchestrates the full command processing pipeline:
        1. Extract intent type from classified intent
        2. Validate device intents are for curtain control
        3. Route to appropriate intent handler
        4. Handler extracts entities, controls devices, and sends response

        Args:
            intent_request: The intent request containing classified intent and client info

        """
        classified_intent = intent_request.classified_intent
        intent_type = classified_intent.intent_type

        self.logger.debug("Processing intent %s with confidence %.2f", intent_type, classified_intent.confidence)

        # Validate device intents are actually for curtain control
        device_intents = {IntentType.DEVICE_SET, IntentType.DEVICE_OPEN, IntentType.DEVICE_CLOSE}
        if intent_type in device_intents and not self._is_curtain_intent(classified_intent):
            self.logger.info("%s intent is not for curtain control, ignoring", intent_type)
            return

        # Route to appropriate handler
        if intent_type == IntentType.DEVICE_OPEN:
            await self._handle_device_open(intent_request)
        elif intent_type == IntentType.DEVICE_CLOSE:
            await self._handle_device_close(intent_request)
        elif intent_type == IntentType.DEVICE_SET:
            await self._handle_device_set(intent_request)
        else:
            self.logger.warning("Unsupported intent type: %s", intent_type)
            await self.send_response(
                "I'm not sure how to handle that request.",
                client_request=intent_request.client_request,
            )
