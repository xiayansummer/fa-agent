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

    ai_api_key: str
    ai_base_url: str = "https://api.anthropic.com/v1"
    ai_model: str = "claude-sonnet-4-6"

    # ASR：DashScope Qwen3-ASR-Flash via OpenAI-compatible /audio/transcriptions
    asr_api_key: str = ""
    asr_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    asr_model: str = "qwen3-asr-flash"

    tavily_api_key: str

    qmingpian_token: str  # open_id for Investarget/企名片 API

    tencent_secret_id: str
    tencent_secret_key: str

    tencent_meeting_app_id: str
    tencent_meeting_secret_id: str
    tencent_meeting_secret_key: str

    internal_api_base: str = "http://127.0.0.1:8000"

    qiniu_ak: str = ""
    qiniu_sk: str = ""
    qiniu_bucket: str = "file"
    qiniu_region: str = "z0"  # 华东-浙江
    qiniu_domain: str = ""    # bound CDN domain for downloads, e.g. https://files.example.com

    token_encrypt_key: str  # Fernet key (44 base64 chars); see .env.example for generation

    tencent_mcp_url: str = "https://mcp.meeting.tencent.com/mcp/wemeet-open/v1"
    tencent_mcp_skill_version: str = "v1.0.7"

settings = Settings()
