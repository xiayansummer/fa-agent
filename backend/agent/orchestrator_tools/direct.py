"""Orchestrator-direct 工具：投资人 CRUD/查询、互动、企名片纪要、腾讯会议管理。
这些工具由 Orchestrator 自己执行，不进入其他 LangGraph 工作流。"""
from __future__ import annotations
import logging
from datetime import datetime
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.ir_users import IRUser
from models.investors import Investor
from models.interaction_logs import InteractionLog
from services import crypto_service
from services.tencent_meeting import TencentMeetingClient, TencentAuthError, TencentToolError
import httpx as _httpx
from skills.qmingpian import (
    qmingpian_search_person,
    qmingpian_search_person_by_phone,
    qmingpian_add_person,
    qmingpian_add_familiar_person,
    qmingpian_update_familiar_person,
    qmingpian_update_person_tags,
    qmingpian_add_person_summary,
    qmingpian_export_ongoing_lunci,
    qmingpian_search_agency,
    qmingpian_add_agency,
    qmingpian_add_agency_summary,
    qmingpian_add_agency_file,
    qmingpian_add_person_card,
    qmingpian_upload_file,
    qmingpian_export_agency,
)


async def _qiniu_to_qmingpian_permanent_url(qiniu_signed_url: str) -> str:
    """Qiniu 签名 URL (24h) → 下载 bytes → 重传企名片 OSS → 拿永久 URL。
    用于名片图和机构文件（BP/DP/TS 等）。"""
    async with _httpx.AsyncClient(timeout=30) as client:
        r = await client.get(qiniu_signed_url)
        if r.status_code != 200:
            raise ValueError(f"从 Qiniu 下载文件失败：HTTP {r.status_code}")
        file_bytes = r.content
        mime = r.headers.get("content-type") or "application/octet-stream"
    filename = qiniu_signed_url.split("/")[-1].split("?")[0] or "file"
    result = await qmingpian_upload_file(file_bytes=file_bytes, filename=filename, mime_type=mime)
    url = result.get("url") or ""
    if not url:
        raise ValueError(f"企名片 OSS 上传返回空 url: {result}")
    return url
from .base import ToolCtx

logger = logging.getLogger(__name__)

AGENT_ROLE = "orchestrator"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "schedule_tencent_meeting",
            "description": (
                "在腾讯会议预订/创建一场会议。当 IR 说「安排会议」「约会议」「开个会」等意图时直接调用，"
                "不要追问。subject 根据用户上下文推断；start_time 没明确时用 30 分钟后的下一个整点；"
                "end_time 默认 start_time 后 1 小时。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "会议主题"},
                    "start_time": {"type": "string", "description": "ISO 8601 开始时间"},
                    "end_time": {"type": "string", "description": "ISO 8601 结束时间，默认 start_time + 1 小时"},
                },
                "required": ["subject", "start_time", "end_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_my_upcoming_meetings",
            "description": "列出当前 IR 即将开始/进行中的腾讯会议，每条含 meeting_id、subject、start_time、end_time。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_tencent_meeting",
            "description": (
                "取消已预订的腾讯会议（不可逆！）。规则：用户首次说取消时**不要直接调用**——先在回复里复述会议主题/会议号，"
                "等用户下一轮明确「确认」「是」「ok」等之后再调用。"
                "**meeting_id 用 schedule_tencent_meeting 返回的 meeting_id（18-20 位长数字），**"
                "**不要传 meeting_code（9-10 位短号，给人看的）**。如果只能拿到短号，"
                "工具内部会自动 fallback 到 list 查询，但优先用长 ID。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "meeting_id": {"type": "string"},
                    "reason_detail": {"type": "string"},
                },
                "required": ["meeting_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_investor",
            "description": (
                "通过姓名/机构关键字搜索投资人。返回 investor_id (本地ID)、person_id、name、agency、position、in_my_library、familiarity。"
                "当用户提到某投资人名字但你不知道 investor_id 时，**先调本工具拿 ID 再做其他操作**。"
            ),
            "parameters": {
                "type": "object",
                "properties": {"keywords": {"type": "string"}},
                "required": ["keywords"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_investor_familiarity",
            "description": (
                "设置投资人熟悉度（本地+企名片双写）。level ∈ "
                "{'未接触','加过微信','见过面','了解投资偏好','跟进过我们的项目','好友'}。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "investor_id": {"type": "integer"},
                    "level": {"type": "string"},
                },
                "required": ["investor_id", "level"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_investor_tags",
            "description": (
                "覆盖式设置投资人在企名片的标签（如「美元」「消费品牌」）。"
                "**至少要 1 个**标签 —— 企名片 API 不支持清空，要清得在 PC 端手动删。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "investor_id": {"type": "integer"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["investor_id", "tags"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_person_summary",
            "description": "为某投资人写一条纪要保存到企名片。建议 50-300 字。",
            "parameters": {
                "type": "object",
                "properties": {
                    "investor_id": {"type": "integer"},
                    "summary": {"type": "string"},
                },
                "required": ["investor_id", "summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_ongoing_project_contacts",
            "description": (
                "查 ongoing 项目的对接清单（机构 + 对接投资人）。"
                "event_name 不传或传空字符串 → 所有 ongoing 项目的全量对接清单。"
                "event_name 传具体项目名（格式如「珀乐互动/A轮/3000万」、「本导基因/B轮/4000万人民币」）"
                "→ 该项目的对接清单。返回 count + 前 30 条预览。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "event_name": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_person_card",
            "description": (
                "把已上传图片绑定为某**已存在投资人**的名片。若不确定该投资人本地是否存在/企名片是否有，"
                "**优先用 bind_business_card**（原子化注册+绑定）。本工具只在已知 investor_id 且本地+企名片都已落地时用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "investor_id": {"type": "integer", "description": "本地 investor 表主键"},
                    "file_url": {"type": "string", "description": "已上传图片的公网 URL"},
                },
                "required": ["investor_id", "file_url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bind_business_card",
            "description": (
                "**名片绑定的统一入口**：传名片图 URL + 投资人姓名 + 可选机构/职务/手机/邮箱/微信，工具内部自动："
                "1) 本地有同名同机构投资人 → 复用；本地无 → 查企名片 → 有就落地到本地表 → 没有就新建企名片+落地本地。"
                "2) 拿到 qmingpian_person_id 后调 addPersonCard 把图绑上。"
                "3) 如果传了 phone/email/wechat/position，已存在的投资人会补全空字段（不覆盖已有值）。"
                "当 IR 上传图片说「关联投资人 X」「这是 X 的名片」时优先用本工具，避免触发 search 失效路径。"
                "用户消息里如果提到电话/邮箱/微信，请一并传进来；没提到留空即可。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_url": {"type": "string", "description": "名片图片公网 URL（必填）"},
                    "name":     {"type": "string", "description": "投资人姓名（必填）"},
                    "agency":   {"type": "string", "description": "机构名"},
                    "position": {"type": "string", "description": "职务（企名片字段 zhiwu）"},
                    "phone":    {"type": "string"},
                    "email":    {"type": "string"},
                    "wechat":   {"type": "string"},
                },
                "required": ["file_url", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_agency",
            "description": (
                "通过关键字搜索企名片机构（多维库 + 外部库合并）。返回前 10 条候选，含 agency_name。"
                "当 IR 提到某机构名但拼写/全称不确定时，**先调本工具消歧**再做 add_agency_summary / add_agency_file。"
            ),
            "parameters": {
                "type": "object",
                "properties": {"keywords": {"type": "string"}},
                "required": ["keywords"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_agency_detail",
            "description": (
                "查机构现有详情：标签、纪要、历史推荐记录。当 IR 说「看一下 X 的现有纪要」「X 机构最近有什么进展」"
                "「X 都对接过哪些项目」等意图时调用。返回 tags / summaries / history。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "agency": {"type": "string", "description": "机构名（同 add_agency_summary 的 agency）"},
                },
                "required": ["agency"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_agency_summary",
            "description": "为某机构写一条纪要保存到企名片机构详情。建议 50-300 字。当 IR 说「给 X 机构记一条纪要」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "agency": {"type": "string", "description": "机构名"},
                    "summary": {"type": "string"},
                },
                "required": ["agency", "summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_agency",
            "description": (
                "新建一家机构到企名片（addAgencyInfo）。幂等：「机构已存在」视为成功。"
                "通常不需要单独调用 —— add_agency_file 内部会自动调它。"
            ),
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_agency_file",
            "description": (
                "给某机构挂一份文件到企名片（BP/DP/Term Sheet 等）。"
                "file_url 必须是公网可访问 URL（一般是先用 /api/upload 上传拿到的 Qiniu URL）。"
                "内部会先调 addAgencyInfo 确保机构存在。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "agency": {"type": "string", "description": "机构名"},
                    "filename": {"type": "string", "description": "显示名（带扩展名）"},
                    "file_url": {"type": "string"},
                },
                "required": ["agency", "filename", "file_url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_interaction",
            "description": (
                "记录一条与投资人的互动。type ∈ {'meeting','call','wechat','email','push','other'}。"
                "occurred_at 不传默认现在。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "investor_id": {"type": "integer"},
                    "type": {"type": "string"},
                    "summary": {"type": "string"},
                    "occurred_at": {"type": "string"},
                    "duration_min": {"type": "integer"},
                    "next_followup_at": {"type": "string"},
                },
                "required": ["investor_id", "type", "summary"],
            },
        },
    },
]


# ============ private helpers ============

async def _get_tencent_client(ctx: ToolCtx) -> Optional[TencentMeetingClient]:
    user = (await ctx.db.execute(select(IRUser).where(IRUser.id == ctx.ir_id))).scalar_one_or_none()
    if not user or not user.tencent_meeting_token_encrypted:
        return None
    try:
        token = crypto_service.decrypt(user.tencent_meeting_token_encrypted)
    except Exception:
        return None
    return TencentMeetingClient(token=token)


async def _resolve_investor(ctx: ToolCtx, investor_id: int) -> Optional[Investor]:
    res = await ctx.db.execute(
        select(Investor).where(Investor.id == investor_id, Investor.is_active == True)
    )
    return res.scalar_one_or_none()


# ============ tool implementations ============

async def _schedule_meeting(args: dict, ctx: ToolCtx) -> dict:
    client = await _get_tencent_client(ctx)
    if client is None:
        return {"error": "IR 未配置腾讯会议 token，请前往「我」→「腾讯会议接入」配置"}
    try:
        result = await client.schedule_meeting(
            subject=args.get("subject", "").strip() or "FA Agent 预订会议",
            start_time=args["start_time"],
            end_time=args["end_time"],
        )
    except TencentAuthError:
        return {"error": "腾讯会议 token 已失效，请重新配置"}
    except TencentToolError as e:
        return {"error": f"腾讯会议返回错误：{e}"}
    except Exception as e:
        return {"error": f"调用失败：{e}"}
    info = result.get("meeting_info_list") or [result]
    first = info[0] if isinstance(info, list) and info else result
    return {
        "ok": True,
        "meeting_code": first.get("meeting_code") or first.get("meeting_id_str") or "",
        "meeting_id": first.get("meeting_id") or "",
        "join_url": first.get("join_url") or "",
        "subject": first.get("subject") or args.get("subject"),
        "start_time": first.get("start_time") or args.get("start_time"),
        "end_time": first.get("end_time") or args.get("end_time"),
        "hint": "已预订成功；腾讯侧索引有 5-10 秒延迟，刚预订的会议在 list_my_upcoming_meetings 里可能短暂不可见。回复用户时直接用上面的 meeting_code/join_url 即可，不必再去 list 验证。",
    }


async def _list_upcoming_meetings(args: dict, ctx: ToolCtx) -> dict:
    client = await _get_tencent_client(ctx)
    if client is None:
        return {"error": "IR 未配置腾讯会议 token"}
    try:
        raw = await client.list_upcoming_meetings()
    except TencentAuthError:
        return {"error": "腾讯会议 token 已失效"}
    except Exception as e:
        return {"error": f"调用失败：{e}"}
    items = [
        {
            "meeting_id": str(m.get("meeting_id") or ""),
            "meeting_code": str(m.get("meeting_code") or m.get("meeting_id_str") or ""),
            "subject": m.get("subject") or "",
            "start_time": m.get("start_time") or "",
            "end_time": m.get("end_time") or "",
        }
        for m in raw
    ]
    return {"ok": True, "meetings": items, "count": len(items)}


async def _cancel_meeting(args: dict, ctx: ToolCtx) -> dict:
    mid_input = (args.get("meeting_id") or "").strip().replace(" ", "")
    if not mid_input:
        return {"error": "meeting_id 不能为空"}
    client = await _get_tencent_client(ctx)
    if client is None:
        return {"error": "IR 未配置腾讯会议 token"}
    # LLM 经常把 meeting_code（9-10 位短号，给人看的）当成 meeting_id 传过来。
    # 真正的 meeting_id 是 18~20 位长数字。短号场景下先查 list 拿真 id。
    real_mid = mid_input
    if len(mid_input) <= 10 and mid_input.isdigit():
        try:
            upcoming = await client.list_upcoming_meetings()
            match = next(
                (m for m in upcoming if str(m.get("meeting_code") or m.get("meeting_id_str") or "") == mid_input),
                None,
            )
            if match and match.get("meeting_id"):
                real_mid = str(match["meeting_id"])
            else:
                return {"error": f"未找到会议号 {mid_input} 对应的会议（可能已结束或非本人预订）"}
        except Exception as e:
            return {"error": f"查询会议失败：{e}"}
    try:
        await client.cancel_meeting(meeting_id=real_mid, reason_detail=args.get("reason_detail", "") or "")
    except TencentAuthError:
        return {"error": "腾讯会议 token 已失效"}
    except TencentToolError as e:
        return {"error": f"腾讯会议返回错误：{e}"}
    except Exception as e:
        return {"error": f"调用失败：{e}"}
    return {"ok": True, "meeting_id": real_mid, "input_was_code": real_mid != mid_input}


async def _search_investor(args: dict, ctx: ToolCtx) -> dict:
    keywords = (args.get("keywords") or "").strip()
    if not keywords:
        return {"error": "keywords 不能为空"}
    try:
        hits = await qmingpian_search_person(keywords)
    except Exception as e:
        return {"error": f"企名片搜索失败：{e}"}
    person_ids = [h.get("person_id") for h in hits if h.get("person_id")]
    local_map: dict[str, Investor] = {}
    if person_ids:
        rows = (await ctx.db.execute(
            select(Investor).where(
                Investor.qmingpian_person_id.in_(person_ids),
                Investor.is_active == True,
            )
        )).scalars().all()
        for inv in rows:
            local_map[inv.qmingpian_person_id] = inv
    seen: set[str] = set()
    items = []
    for h in hits:
        pid = h.get("person_id")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        local = local_map.get(pid)
        items.append({
            "investor_id": local.id if local else None,
            "person_id": pid,
            "name": h.get("name", ""),
            "agency": h.get("agency", ""),
            "position": h.get("zhiwu") or "",
            "in_my_library": local is not None,
            "familiarity": local.familiarity if local else None,
        })
    return {"ok": True, "count": len(items), "results": items[:10]}


_FAMILIARITY_LEVELS = {"未接触", "加过微信", "见过面", "了解投资偏好", "跟进过我们的项目", "好友"}


async def _set_familiarity(args: dict, ctx: ToolCtx) -> dict:
    inv_id = args.get("investor_id")
    level = (args.get("level") or "").strip()
    if not inv_id or not level:
        return {"error": "investor_id 和 level 都必填"}
    if level not in _FAMILIARITY_LEVELS:
        return {"error": f"level 必须是 {sorted(_FAMILIARITY_LEVELS)}"}
    inv = await _resolve_investor(ctx, inv_id)
    if not inv:
        return {"error": f"investor_id={inv_id} 不存在"}
    prev = inv.familiarity
    ir_row = (await ctx.db.execute(select(IRUser).where(IRUser.id == ctx.ir_id))).scalar_one_or_none()
    if ir_row and ir_row.qmingpian_username and inv.qmingpian_person_id:
        try:
            fn = qmingpian_update_familiar_person if prev else qmingpian_add_familiar_person
            await fn(name=inv.name, agency=inv.agency or "", user_name=ir_row.qmingpian_username, level=level)
        except Exception as e:
            logger.warning("familiarity sync to qmingpian failed: %s", e)
            return {"error": f"企名片同步失败：{e}"}
    inv.familiarity = level
    await ctx.db.commit()
    return {"ok": True, "investor_id": inv_id, "name": inv.name, "level": level}


async def _set_tags(args: dict, ctx: ToolCtx) -> dict:
    inv_id = args.get("investor_id")
    tags = args.get("tags")
    if not inv_id or tags is None:
        return {"error": "investor_id 和 tags 都必填"}
    if not isinstance(tags, list):
        return {"error": "tags 必须是字符串数组"}
    cleaned = [t.strip() for t in tags if isinstance(t, str) and t.strip()]
    if not cleaned:
        return {"error": "tags 不能为空 —— 企名片 API 不支持清空标签，需在 PC 端手动删除"}
    inv = await _resolve_investor(ctx, inv_id)
    if not inv:
        return {"error": f"investor_id={inv_id} 不存在"}
    try:
        await qmingpian_update_person_tags(name=inv.name, agency=inv.agency or "", tags=cleaned)
    except Exception as e:
        return {"error": f"企名片同步失败：{e}"}
    return {"ok": True, "investor_id": inv_id, "name": inv.name, "tags": cleaned}


async def _add_summary(args: dict, ctx: ToolCtx) -> dict:
    inv_id = args.get("investor_id")
    summary = (args.get("summary") or "").strip()
    if not inv_id or not summary:
        return {"error": "investor_id 和 summary 都必填"}
    inv = await _resolve_investor(ctx, inv_id)
    if not inv:
        return {"error": f"investor_id={inv_id} 不存在"}
    ir_row = (await ctx.db.execute(select(IRUser).where(IRUser.id == ctx.ir_id))).scalar_one_or_none()
    if not ir_row or not ir_row.qmingpian_username:
        return {"error": "当前 IR 未配置企名片用户名"}
    try:
        await qmingpian_add_person_summary(
            name=inv.name, agency=inv.agency or "",
            summary=summary, user_name=ir_row.qmingpian_username,
        )
    except Exception as e:
        return {"error": f"企名片写入纪要失败：{e}"}
    return {"ok": True, "investor_id": inv_id, "name": inv.name, "summary_preview": summary[:60]}


_AGENCY_PREFIXES = sorted(
    ["珠海", "上海", "北京", "深圳", "广州", "杭州", "成都", "南京", "苏州",
     "天津", "重庆", "宁波", "厦门", "西安", "武汉", "长沙", "香港", "澳门",
     "广东省", "浙江省", "江苏省", "山东省", "福建省"],
    key=len, reverse=True,
)


def _agency_brand(s: str) -> str:
    """提取机构品牌词（前 2 个汉字，去掉常见地名前缀）。"""
    s = (s or "").strip()
    if not s:
        return ""
    for pre in _AGENCY_PREFIXES:
        if s.startswith(pre):
            s = s[len(pre):]
            break
    return s[:2]


def _same_agency(a: str, b: str) -> bool:
    """机构名 fuzzy 比较：处理"鲸芯投资" vs "珠海鲸芯创业投资管理有限公司"。"""
    a = (a or "").strip()
    b = (b or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 2 and a in b:
        return True
    if len(b) >= 2 and b in a:
        return True
    ba, bb = _agency_brand(a), _agency_brand(b)
    return bool(ba) and ba == bb


async def _supplement_investor_fields(
    ctx: ToolCtx, inv: Investor,
    phone: str, email: str, wechat: str, position: str, agency: str,
) -> None:
    """对已存在投资人补全空字段，不覆盖已有值。"""
    changed = False
    if phone and not (inv.phone or []):
        inv.phone = [phone]; changed = True
    if email and not (inv.email or []):
        inv.email = [email]; changed = True
    if wechat and not (inv.wechat or []):
        inv.wechat = [wechat]; changed = True
    if position and not inv.position:
        inv.position = position; changed = True
    if agency and not inv.agency:
        inv.agency = agency; changed = True
    if changed:
        await ctx.db.commit()


async def _bind_business_card(args: dict, ctx: ToolCtx) -> dict:
    file_url = (args.get("file_url") or "").strip()
    name = (args.get("name") or "").strip()
    if not file_url or not name:
        return {"error": "file_url 和 name 都必填"}
    agency = (args.get("agency") or "").strip()
    position = (args.get("position") or "").strip()
    phone = (args.get("phone") or "").strip()
    email = (args.get("email") or "").strip()
    wechat = (args.get("wechat") or "").strip()

    ir_row = (await ctx.db.execute(select(IRUser).where(IRUser.id == ctx.ir_id))).scalar_one_or_none()
    if not ir_row or not ir_row.qmingpian_username:
        return {"error": "当前 IR 未配置企名片用户名"}

    person_id: Optional[str] = None
    inv: Optional[Investor] = None
    created_local = False
    created_qmp = False

    # 阶段 1a：优先用手机号查（唯一定位，绕开同名歧义）
    qmp_match = None
    if phone:
        try:
            phone_hits = await qmingpian_search_person_by_phone(phone)
            for h in phone_hits:
                if (h.get("name") or "") == name:
                    qmp_match = h
                    break
            if qmp_match is None and len(phone_hits) == 1:
                qmp_match = phone_hits[0]  # 该手机号只对应一个人，即使姓名 OCR 偏差也认
        except Exception:
            pass  # 手机查失败 fallback 到 name 查

    # 阶段 1b：手机没命中 → 用姓名查（fuzzy agency 匹配）
    qmp_hits: list = []
    if qmp_match is None:
        try:
            qmp_hits = await qmingpian_search_person(name)
        except Exception as e:
            return {"error": f"企名片搜索失败：{e}"}
        for h in qmp_hits:
            if (h.get("name") or "") != name:
                continue
            if agency and _same_agency(h.get("agency") or "", agency):
                qmp_match = h
                break
        if qmp_match is None:
            qmp_match = next((h for h in qmp_hits if (h.get("name") or "") == name), None)

    permanent_card_url: Optional[str] = None
    if qmp_match:
        person_id = qmp_match.get("person_id")
    else:
        # 阶段 3：企名片没有 → 先把名片中转到企名片 OSS，再调 addPersonInfo 一并绑名片
        try:
            permanent_card_url = await _qiniu_to_qmingpian_permanent_url(file_url)
        except Exception as e:
            return {"error": f"图片中转企名片失败：{e}"}
        try:
            add_result = await qmingpian_add_person(
                name=name, agency=agency,
                phone=phone, wechat=wechat, email=email,
                position=position,
                card_url=permanent_card_url,
            )
            created_qmp = True
            person_id = add_result.get("person_id") if isinstance(add_result, dict) else None
            # fallback：返回里没 person_id 就重搜（理论上不该发生）
            if not person_id:
                hits2 = await qmingpian_search_person(name)
                for h in hits2:
                    if (h.get("name") or "") != name:
                        continue
                    if agency and _same_agency(h.get("agency") or "", agency):
                        person_id = h.get("person_id"); break
                if not person_id and hits2:
                    person_id = next((h.get("person_id") for h in hits2
                                       if (h.get("name") or "") == name), None)
        except Exception as e:
            return {"error": f"企名片新建投资人失败：{e}"}

    if not person_id:
        return {"error": "无法从企名片拿到 person_id"}

    # 阶段 4：用 person_id 强匹配本地（避免简称/全称 agency 不一致重复建）
    inv = (await ctx.db.execute(
        select(Investor).where(
            Investor.qmingpian_person_id == person_id,
            Investor.is_active == True,
        )
    )).scalar_one_or_none()

    # 阶段 5：本地按 person_id 没命中 → 按 name + agency-fuzzy 在本地找
    # 找到就把 person_id 回填，避免再次重复
    if inv is None:
        same_name = (await ctx.db.execute(
            select(Investor).where(
                Investor.is_active == True,
                Investor.name == name,
            ).limit(10)
        )).scalars().all()
        # 优先 agency fuzzy match
        candidate_agency = agency or (qmp_match.get("agency") if qmp_match else "")
        for c in same_name:
            if c.qmingpian_person_id and c.qmingpian_person_id != person_id:
                continue  # 已绑别人不能复用
            if _same_agency(c.agency or "", candidate_agency):
                inv = c
                break
        # 还没命中且只有一条同名候选且未绑 person_id → 复用
        if inv is None and len(same_name) == 1 and not same_name[0].qmingpian_person_id:
            inv = same_name[0]
        if inv is not None and not inv.qmingpian_person_id:
            inv.qmingpian_person_id = person_id
            await ctx.db.commit()

    # 阶段 6：本地依然没有 → 新建
    if inv is None:
        inv = Investor(
            qmingpian_person_id=person_id,
            name=name,
            agency=agency or (qmp_match.get("agency") if qmp_match else None),
            position=position or (qmp_match.get("zhiwu") if qmp_match else None),
            phone=[phone] if phone else None,
            email=[email] if email else None,
            wechat=[wechat] if wechat else None,
            is_active=True,
        )
        ctx.db.add(inv)
        await ctx.db.commit()
        await ctx.db.refresh(inv)
        created_local = True
    else:
        await _supplement_investor_fields(ctx, inv, phone, email, wechat, position, agency)

    if not person_id:
        return {"error": f"本地 investor {inv.id} 没有 qmingpian_person_id，无法绑定名片"}

    # 5) 绑名片 —— 新建场景已经在 addPersonInfo(card_url=...) 一步绑过，跳过
    if not created_qmp:
        try:
            permanent_card_url = await _qiniu_to_qmingpian_permanent_url(file_url)
        except Exception as e:
            return {"error": f"图片中转企名片失败：{e}",
                    "investor_id": inv.id, "created_local": created_local}
        try:
            await qmingpian_add_person_card(
                person_id=person_id, img_url=permanent_card_url,
                create_name=ir_row.qmingpian_username,
            )
        except Exception as e:
            return {"error": f"企名片绑定名片失败：{e}",
                    "investor_id": inv.id, "created_local": created_local}

    return {
        "ok": True,
        "investor_id": inv.id,
        "name": inv.name,
        "agency": inv.agency,
        "qmingpian_person_id": person_id,
        "created_local": created_local,
        "created_in_qmingpian": created_qmp,
        "fields_supplied": {
            "phone": bool(phone), "email": bool(email),
            "wechat": bool(wechat), "position": bool(position),
        },
    }


async def _add_person_card(args: dict, ctx: ToolCtx) -> dict:
    inv_id = args.get("investor_id")
    file_url = (args.get("file_url") or "").strip()
    if not inv_id or not file_url:
        return {"error": "investor_id 和 file_url 都必填"}
    inv = await _resolve_investor(ctx, inv_id)
    if not inv:
        return {"error": f"investor_id={inv_id} 不存在"}
    if not inv.qmingpian_person_id:
        return {"error": f"投资人 {inv.name} 在企名片侧无 person_id，无法绑定名片"}
    ir_row = (await ctx.db.execute(select(IRUser).where(IRUser.id == ctx.ir_id))).scalar_one_or_none()
    if not ir_row or not ir_row.qmingpian_username:
        return {"error": "当前 IR 未配置企名片用户名"}
    try:
        permanent_url = await _qiniu_to_qmingpian_permanent_url(file_url)
    except Exception as e:
        return {"error": f"图片中转企名片失败：{e}"}
    try:
        await qmingpian_add_person_card(
            person_id=inv.qmingpian_person_id,
            img_url=permanent_url,
            create_name=ir_row.qmingpian_username,
        )
    except Exception as e:
        return {"error": f"企名片绑定名片失败：{e}"}
    return {"ok": True, "investor_id": inv_id, "name": inv.name}


def _extract_agency_name(raw) -> tuple[str, str]:
    """企名片两个 search 接口的返回有时是 str、有时是 dict —— 统一抽 (name, uuid)。"""
    if isinstance(raw, str):
        return raw, ""
    if isinstance(raw, dict):
        name = raw.get("name") or raw.get("agency") or raw.get("agency_name") or ""
        return name, (raw.get("uuid") or "")
    return "", ""


async def _search_agency(args: dict, ctx: ToolCtx) -> dict:
    keywords = (args.get("keywords") or "").strip()
    if not keywords:
        return {"error": "keywords 不能为空"}
    items: list[dict] = []
    seen: set[str] = set()
    try:
        hits = await qmingpian_search_agency(keywords)
        for h in hits[:10]:
            name, uuid = _extract_agency_name(h)
            if not name or name in seen:
                continue
            seen.add(name)
            items.append({"agency_name": name, "uuid": uuid, "source": "multi"})
    except Exception as e:
        logger.warning("search_agency multi failed: %s", e)
    if len(items) < 10:
        try:
            from skills.qmingpian import qmingpian_search_external_agency
            ext = await qmingpian_search_external_agency(keywords)
            for raw in ext:
                name, _ = _extract_agency_name(raw)
                if not name or name in seen:
                    continue
                seen.add(name)
                items.append({"agency_name": name, "source": "external"})
                if len(items) >= 10:
                    break
        except Exception as e:
            logger.warning("search_agency external failed: %s", e)
    return {"ok": True, "count": len(items), "results": items}


async def _get_agency_detail(args: dict, ctx: ToolCtx) -> dict:
    agency = (args.get("agency") or "").strip()
    if not agency:
        return {"error": "agency 不能为空"}
    try:
        data = await qmingpian_export_agency(agency)
    except Exception as e:
        return {"error": f"企名片机构详情导出失败：{e}"}
    summaries = data.get("summaries") or []
    history = data.get("history") or []
    return {
        "ok": True,
        "agency": agency,
        "tags": data.get("tags") or [],
        "summary_count": len(summaries),
        "summaries": summaries[:10],   # 限 10 条避免压 LLM context
        "history_count": len(history),
        "history": history[:10],
    }


async def _add_agency_summary(args: dict, ctx: ToolCtx) -> dict:
    agency = (args.get("agency") or "").strip()
    summary = (args.get("summary") or "").strip()
    if not agency or not summary:
        return {"error": "agency 和 summary 都必填"}
    ir_row = (await ctx.db.execute(select(IRUser).where(IRUser.id == ctx.ir_id))).scalar_one_or_none()
    if not ir_row or not ir_row.qmingpian_username:
        return {"error": "当前 IR 未配置企名片用户名"}
    try:
        await qmingpian_add_agency_summary(
            agency=agency, summary=summary, user_name=ir_row.qmingpian_username,
        )
    except Exception as e:
        return {"error": f"企名片写入机构纪要失败：{e}"}
    return {"ok": True, "agency": agency, "summary_preview": summary[:60]}


async def _add_agency(args: dict, ctx: ToolCtx) -> dict:
    name = (args.get("name") or "").strip()
    if not name:
        return {"error": "name 不能为空"}
    try:
        data = await qmingpian_add_agency(name)
    except Exception as e:
        return {"error": f"企名片新增机构失败：{e}"}
    return {"ok": True, "agency": name, "existed": bool(data.get("existed"))}


async def _add_agency_file(args: dict, ctx: ToolCtx) -> dict:
    agency = (args.get("agency") or "").strip()
    filename = (args.get("filename") or "").strip()
    file_url = (args.get("file_url") or "").strip()
    if not agency or not filename or not file_url:
        return {"error": "agency / filename / file_url 都必填"}
    ir_row = (await ctx.db.execute(select(IRUser).where(IRUser.id == ctx.ir_id))).scalar_one_or_none()
    user_name = ir_row.qmingpian_username if ir_row else ""
    try:
        permanent_url = await _qiniu_to_qmingpian_permanent_url(file_url)
    except Exception as e:
        return {"error": f"文件中转企名片失败：{e}"}
    try:
        await qmingpian_add_agency_file(
            agency_name=agency, filename=filename, file_url=permanent_url, user_name=user_name or "",
        )
    except Exception as e:
        return {"error": f"企名片挂载机构文件失败：{e}"}
    return {"ok": True, "agency": agency, "filename": filename}


async def _list_ongoing(args: dict, ctx: ToolCtx) -> dict:
    event_name = (args.get("event_name") or "").strip()
    try:
        data = await qmingpian_export_ongoing_lunci(event_name)
    except Exception as e:
        return {"error": f"企名片导出失败：{e}"}
    contacts = data.get("contacts", []) or []
    return {
        "ok": True,
        "event_name": event_name or "（全部 ongoing）",
        "total": data.get("count", len(contacts)),
        "preview": contacts[:30],   # 限 30 条避免压 LLM context
        "truncated": len(contacts) > 30,
    }


_INTERACTION_TYPES = {"meeting", "call", "wechat", "email", "push", "other"}


async def _record_interaction(args: dict, ctx: ToolCtx) -> dict:
    inv_id = args.get("investor_id")
    itype = (args.get("type") or "").strip()
    summary = (args.get("summary") or "").strip()
    if not inv_id or not itype or not summary:
        return {"error": "investor_id, type, summary 都必填"}
    if itype not in _INTERACTION_TYPES:
        return {"error": f"type 必须是 {_INTERACTION_TYPES}"}
    inv = await _resolve_investor(ctx, inv_id)
    if not inv:
        return {"error": f"investor_id={inv_id} 不存在"}
    occurred_at = args.get("occurred_at")
    try:
        occ_dt = datetime.fromisoformat(occurred_at) if occurred_at else datetime.now()
    except ValueError:
        return {"error": f"occurred_at 格式无效：{occurred_at}"}
    nxt = args.get("next_followup_at")
    try:
        nxt_dt = datetime.fromisoformat(nxt) if nxt else None
    except ValueError:
        return {"error": f"next_followup_at 格式无效：{nxt}"}
    log = InteractionLog(
        investor_id=inv_id, ir_id=ctx.ir_id, type=itype,
        occurred_at=occ_dt,
        duration_min=args.get("duration_min"),
        summary=summary,
        next_followup_at=nxt_dt,
        agent_generated=False,
    )
    ctx.db.add(log)
    if not inv.last_interaction_at or occ_dt > inv.last_interaction_at:
        inv.last_interaction_at = occ_dt
    await ctx.db.commit()
    await ctx.db.refresh(log)
    return {"ok": True, "interaction_id": log.id, "investor_id": inv_id, "name": inv.name, "type": itype}


_DISPATCH = {
    "schedule_tencent_meeting":  _schedule_meeting,
    "list_my_upcoming_meetings": _list_upcoming_meetings,
    "cancel_tencent_meeting":    _cancel_meeting,
    "search_investor":           _search_investor,
    "set_investor_familiarity":  _set_familiarity,
    "set_investor_tags":         _set_tags,
    "add_person_summary":        _add_summary,
    "record_interaction":        _record_interaction,
    "list_ongoing_project_contacts": _list_ongoing,
    "search_agency":             _search_agency,
    "get_agency_detail":         _get_agency_detail,
    "add_agency_summary":        _add_agency_summary,
    "add_agency":                _add_agency,
    "add_agency_file":           _add_agency_file,
    "add_person_card":           _add_person_card,
    "bind_business_card":        _bind_business_card,
}


async def dispatch(name: str, args: dict, ctx: ToolCtx) -> dict:
    fn = _DISPATCH.get(name)
    if fn is None:
        return {"error": f"direct: 未知工具 {name}"}
    return await fn(args, ctx)
