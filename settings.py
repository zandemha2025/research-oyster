from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    twitch_client_id: str = ""
    twitch_client_secret: str = ""
    kick_client_id: str = ""
    kick_client_secret: str = ""
    x_bearer_token: str = ""
    apify_token: str = ""
    discord_bot_token: str = ""
    collection_hour_utc: int = Field(default=16, ge=0, le=23)
    http_timeout_seconds: float = Field(default=20.0, gt=0)
    max_retries: int = Field(default=4, ge=0)
    twitch_stream_pages: int = Field(default=10, ge=1, le=10)
    kick_stream_pages: int = Field(default=10, ge=1, le=100)
    output_dir: Path = Path("output")
