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
    ai_base_url: str = "https://opencode.ai/zen/go/v1"
    ai_model: str = "qwen3.7-plus"
    # 名片/图片需要视觉能力，minimax 不支持，单独用多模态模型（同 opencode 端点+key）
    vision_model: str = "qwen3.7-plus"

    # ASR：DashScope Qwen3-ASR-Flash via OpenAI-compatible /audio/transcriptions
    asr_api_key: str = ""
    asr_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    asr_model: str = "qwen3-asr-flash"

    # 日程提醒订阅消息模板（小程序后台「订阅消息」里选的"日程提醒"类模板）
    wx_schedule_tmpl_id: str = "J1qIizvW8rJkrD4CZfKK6ldUcuOB3E6kiERojkOYXjU"
    # 点击服务通知打开哪个版本：developer/trial/formal。上线正式版后改 formal（.env 可覆盖）
    wx_miniprogram_state: str = "trial"

    tavily_api_key: str

    qmingpian_token: str  # open_id for Investarget/企名片 API
    qmingpian_team_uuid: str = ""   # team_uuid for /Person/addPersonCard
    qmingpian_unionid: str = ""     # unionid for /Person/addPersonCard

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
    tencent_mcp_skill_version: str = "v1.1.0"  # v1.0.7 被腾讯服务端硬拦截

settings = Settings()
