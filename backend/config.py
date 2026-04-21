from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mysql_url: str
    redis_url: str

    wechat_appid: str
    wechat_secret: str

    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_expire_days: int = 7

    claude_api_key: str
    claude_model: str = "claude-sonnet-4-6"

    tavily_api_key: str

    qmingpian_api_url: str
    qmingpian_token: str

    tencent_secret_id: str
    tencent_secret_key: str

    tencent_meeting_app_id: str
    tencent_meeting_secret_id: str
    tencent_meeting_secret_key: str

settings = Settings()
