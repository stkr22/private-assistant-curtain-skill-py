"""Entry point for the curtain skill service."""

import asyncio
import pathlib
from typing import Annotated

import jinja2
import typer
from private_assistant_commons import MqttConfig, mqtt_connection_handler, skill_config, skill_logger
from private_assistant_commons.database import create_skill_engine

from private_assistant_curtain_skill import curtain_skill

app = typer.Typer()


@app.command()
def main(config_path: Annotated[pathlib.Path, typer.Argument(envvar="PRIVATE_ASSISTANT_CONFIG_PATH")]) -> None:
    """Run the curtain skill with the provided configuration.

    Args:
        config_path: Path to the skill configuration file

    """
    asyncio.run(start_skill(config_path))


async def start_skill(
    config_path: pathlib.Path,
):
    """Initialize and start the curtain skill with MQTT connection.

    Args:
        config_path: Path to the skill configuration file

    """
    # Set up logger early on
    logger = skill_logger.SkillLogger.get_logger("Private Assistant CurtainSkill")

    # Load configuration
    config_obj = skill_config.load_config(config_path, skill_config.SkillConfig)

    # Create an async database engine for global device registry
    # AIDEV-NOTE: create_skill_engine provides automatic pool resilience (pool_pre_ping, pool_recycle, command_timeout)
    db_engine_async = create_skill_engine()

    # AIDEV-NOTE: No custom table creation needed - global device registry tables
    # are managed by BaseSkill and commons library

    # Set up Jinja2 template environment
    template_env = jinja2.Environment(
        loader=jinja2.PackageLoader(
            "private_assistant_curtain_skill",
            "templates",
        )
    )

    # Start the skill using the async MQTT connection handler
    await mqtt_connection_handler.mqtt_connection_handler(
        curtain_skill.CurtainSkill,
        config_obj,
        mqtt_config=MqttConfig(),
        retry_interval=5,
        logger=logger,
        template_env=template_env,
        engine=db_engine_async,
    )


if __name__ == "__main__":
    app()
