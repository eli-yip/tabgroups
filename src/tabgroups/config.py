"""LLM endpoint configuration for `classify`.

Settings come from a TOML file and/or `TABGROUPS_*` environment variables, with
env taking precedence. The TOML path is fixed at construction by `load_settings`,
which points a `LLMSettings` subclass at the chosen config file.
"""

from pathlib import Path

import typer
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)
from rich.console import Console

err = Console(stderr=True)


class LLMSettings(BaseSettings):
    """OpenAI-compatible endpoint settings, from env (`TABGROUPS_*`) and/or TOML.

    The TOML path is taken from `model_config["toml_file"]`; `load_settings`
    builds a subclass that points it at the chosen config file.
    """

    model_config = SettingsConfigDict(
        env_prefix="TABGROUPS_", extra="ignore", toml_file="config.toml"
    )

    base_url: str
    api_key: str
    model: str

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # priority: init kwargs > env > .env > TOML file (from model_config)
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )


def load_settings(config_path: Path) -> LLMSettings:
    """Build settings reading the given TOML file (env still overrides it)."""

    class _Configured(LLMSettings):
        model_config = SettingsConfigDict(
            env_prefix="TABGROUPS_", extra="ignore", toml_file=config_path
        )

    try:
        # ty treats the no-default fields as required constructor args; it can't
        # see that pydantic-settings fills them from env/TOML, so the no-arg call
        # is flagged. Correct at runtime — ignore just this rule.
        return _Configured()  # ty: ignore[missing-argument]  # pyright: ignore[reportCallIssue]
    except Exception as e:  # missing base_url/api_key/model surface here
        err.print(
            f"[red]error:[/] incomplete LLM config: {e}\n"
            f"set base_url/api_key/model in [bold]{config_path}[/] or via "
            "[bold]TABGROUPS_BASE_URL / TABGROUPS_API_KEY / TABGROUPS_MODEL[/]."
        )
        raise typer.Exit(1) from e
