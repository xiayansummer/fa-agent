from __future__ import annotations
import json
import logging
import httpx
from typing import Optional
from config import settings

logger = logging.getLogger(__name__)


class TencentAuthError(Exception):
    """Token 无效或被拒绝"""

class TencentToolError(Exception):
    """MCP 工具执行失败（业务逻辑错误，如缺录制）"""
    def __init__(self, message: str, raw: dict):
        super().__init__(message)
        self.raw = raw


_DEFAULT_CLIENT_INFO = {
    "os": "linux",
    "agent": "fa-agent-backend",
    "model": "qwen3.6-plus",
}

# X-Skill-Version 候选列表 —— 腾讯偶尔硬拦截特定版本，自动探测哪个可用。
_VERSION_CANDIDATES = ["v1.1.0", "v1.1.1", "v1.2.0", "v1.0.8", "v2.0.0"]
_SKILL_VERSION_CACHE_KEY = "tencent:skill_version"
_SKILL_VERSION_TTL = 86400  # 每天一次探测


async def _is_version_rejected(text: str) -> bool:
    return "已过期" in text or "强制拦截" in text


async def _resolve_skill_version(token: str) -> str:
    """每日首次时探测哪个 skill 版本可用，结果缓存 Redis 24h。
    探测策略：先试 settings 默认值；被拒则按候选列表逐一探测；都不行就用 settings 默认硬扛。"""
    try:
        from redis_client import get_redis
        redis = await get_redis()
        cached = await redis.get(_SKILL_VERSION_CACHE_KEY)
        if cached:
            return cached
    except Exception as e:
        logger.warning("redis get skill_version cache failed: %s", e)
        redis = None

    seen: set[str] = set()
    pool = [settings.tencent_mcp_skill_version, *_VERSION_CANDIDATES]
    for v in pool:
        if not v or v in seen:
            continue
        seen.add(v)
        body = {"jsonrpc": "2.0", "method": "tools/call",
                "params": {"name": "convert_timestamp", "arguments": {"_client_info": _DEFAULT_CLIENT_INFO}},
                "id": 1}
        headers = {"Content-Type": "application/json",
                   "X-Tencent-Meeting-Token": token,
                   "X-Skill-Version": v}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(settings.tencent_mcp_url, json=body, headers=headers)
            if resp.status_code != 200:
                continue
            if await _is_version_rejected(resp.text):
                logger.info("tencent skill version %s rejected: %s", v, resp.text[:80])
                continue
        except Exception as e:
            logger.warning("probe version %s failed: %s", v, e)
            continue
        logger.info("tencent skill version resolved: %s (cached 24h)", v)
        if redis is not None:
            try:
                await redis.setex(_SKILL_VERSION_CACHE_KEY, _SKILL_VERSION_TTL, v)
            except Exception:
                pass
        return v

    # fallback：所有候选都不可用，硬扛 settings 默认；下次再探测
    return settings.tencent_mcp_skill_version


class TencentMeetingClient:
    """Per-IR Tencent Meeting MCP 客户端，stateless（每个请求独立）。"""

    def __init__(self, token: str, timeout: float = 30.0):
        self._token = token
        self._timeout = timeout

    async def _call(self, tool_name: str, arguments: dict) -> dict:
        """调一个 MCP 工具，返回 body 字典。"""
        args = {**arguments, "_client_info": _DEFAULT_CLIENT_INFO}
        body = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": args},
            "id": 1,
        }
        version = await _resolve_skill_version(self._token)
        headers = {
            "Content-Type": "application/json",
            "X-Tencent-Meeting-Token": self._token,
            "X-Skill-Version": version,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(settings.tencent_mcp_url, json=body, headers=headers)
        if resp.status_code == 401:
            raise TencentAuthError("token 无效或已过期")
        resp.raise_for_status()
        # 服务端再次硬拦截当前版本（理论上 _resolve 已过滤）→ 失效缓存重试一次
        if await _is_version_rejected(resp.text):
            try:
                from redis_client import get_redis
                _r = await get_redis()
                await _r.delete(_SKILL_VERSION_CACHE_KEY)
            except Exception:
                pass
            new_v = await _resolve_skill_version(self._token)
            if new_v != version:
                headers["X-Skill-Version"] = new_v
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(settings.tencent_mcp_url, json=body, headers=headers)
        data = resp.json()
        if "error" in data.get("result", {}):
            err = data["result"]["error"]
            raise TencentToolError(err.get("message", "tool failed"), data)
        # MCP 返回结构：result.content[0].text 是 JSON 字符串
        try:
            text = data["result"]["content"][0]["text"]
            outer = json.loads(text)
            # 部分工具的 outer 还包一层 body（也是 JSON 字符串）
            if "body" in outer and isinstance(outer["body"], str):
                return json.loads(outer["body"])
            return outer
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise TencentToolError(f"unexpected response shape: {e}", data)

    async def verify_token(self) -> bool:
        """轻量验证 token 是否可用。返回 True/False，不抛 auth 错。"""
        try:
            await self._call("convert_timestamp", {})
            return True
        except (TencentAuthError, TencentToolError):
            return False

    async def list_ended_meetings(
        self,
        start_time: str,
        end_time: str,
        page_size: int = 20,
    ) -> list[dict]:
        """已结束的会议（最近 N 天，最多 31 天范围）。"""
        result = await self._call("get_user_ended_meetings", {
            "start_time": start_time,
            "end_time": end_time,
            "page_size": page_size,
        })
        return result.get("meeting_info_list", [])

    async def list_upcoming_meetings(self) -> list[dict]:
        """即将开始/进行中的会议。"""
        result = await self._call("get_user_meetings", {})
        return result.get("meeting_info_list", [])

    async def get_records_list(self, meeting_id: str) -> list[dict]:
        """会议的录制文件列表（拿 record_file_id 用）。"""
        result = await self._call("get_records_list", {"meeting_id": meeting_id})
        # 字段名在 spike 中观察过：'record_meetings' 或 'meeting_record_list'
        return result.get("record_meetings") or result.get("meeting_record_list") or []

    async def get_smart_minutes(self, record_file_id: str, lang: str = "zh") -> str:
        """智能纪要原文。"""
        result = await self._call("get_smart_minutes", {
            "record_file_id": record_file_id,
            "lang": lang,
        })
        # 返回 dict 里 minutes 字段或类似，spike 没充分验证因为没录制
        return result.get("minutes") or result.get("smart_minutes") or json.dumps(result, ensure_ascii=False)

    async def schedule_meeting(
        self,
        subject: str,
        start_time: str,
        end_time: str,
        password: str = "",
        meeting_type: int = 0,
    ) -> dict:
        """创建/预订一场会议。subject/start_time/end_time 必填。
        start_time/end_time 是 ISO 8601 字符串（如 '2026-05-13T15:30:00+08:00'）。
        返回 dict 含 meeting_code / join_url / meeting_id 等。"""
        args = {
            "subject": subject,
            "start_time": start_time,
            "end_time": end_time,
            "meeting_type": meeting_type,
        }
        if password:
            args["password"] = password
        return await self._call("schedule_meeting", args)

    async def cancel_meeting(
        self,
        meeting_id: str,
        reason_code: int = 1,
        reason_detail: str = "",
    ) -> dict:
        """取消一场已预订/进行中的会议。meeting_id 必填。
        reason_code: 取消原因码，默认 1。reason_detail: 文字原因，可选。
        """
        args: dict = {"meeting_id": meeting_id, "reason_code": reason_code}
        if reason_detail:
            args["reason_detail"] = reason_detail
        return await self._call("cancel_meeting", args)
