from __future__ import annotations
import json
import httpx
from typing import Optional
from config import settings


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
        headers = {
            "Content-Type": "application/json",
            "X-Tencent-Meeting-Token": self._token,
            "X-Skill-Version": settings.tencent_mcp_skill_version,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(settings.tencent_mcp_url, json=body, headers=headers)
        if resp.status_code == 401:
            raise TencentAuthError("token 无效或已过期")
        resp.raise_for_status()
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
