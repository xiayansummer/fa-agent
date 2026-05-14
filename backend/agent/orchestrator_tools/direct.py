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
from skills.qmingpian import (
    qmingpian_search_person,
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
)
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
                "等用户下一轮明确「确认」「是」「ok」等之后再调用。若用户直接给出明确 meeting_id 且语气坚定，可一次性调用。"
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
            "description": "覆盖式设置投资人在企名片的标签（如「美元」「消费品牌」）。tags=[] 表示清空。",
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
                "把已上传到 Qiniu 的名片图片绑定到某投资人（企名片 PC 端能看到这张名片）。"
                "当 IR 上传图片并说「关联给 X」「这是 X 的名片」「挂到投资人 X」等意图时调用。"
                "若不知道 investor_id，先用 search_investor 拿；本工具需要本地 investor_id（不是企名片 person_id，会自动转换）。"
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
    mid = (args.get("meeting_id") or "").strip()
    if not mid:
        return {"error": "meeting_id 不能为空"}
    client = await _get_tencent_client(ctx)
    if client is None:
        return {"error": "IR 未配置腾讯会议 token"}
    try:
        await client.cancel_meeting(meeting_id=mid, reason_detail=args.get("reason_detail", "") or "")
    except TencentAuthError:
        return {"error": "腾讯会议 token 已失效"}
    except TencentToolError as e:
        return {"error": f"腾讯会议返回错误：{e}"}
    except Exception as e:
        return {"error": f"调用失败：{e}"}
    return {"ok": True, "meeting_id": mid}


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


async def _set_familiarity(args: dict, ctx: ToolCtx) -> dict:
    inv_id = args.get("investor_id")
    level = (args.get("level") or "").strip()
    if not inv_id or not level:
        return {"error": "investor_id 和 level 都必填"}
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
    inv = await _resolve_investor(ctx, inv_id)
    if not inv:
        return {"error": f"investor_id={inv_id} 不存在"}
    try:
        await qmingpian_update_person_tags(name=inv.name, agency=inv.agency or "", tags=tags)
    except Exception as e:
        return {"error": f"企名片同步失败：{e}"}
    return {"ok": True, "investor_id": inv_id, "name": inv.name, "tags": tags}


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
        await qmingpian_add_person_card(
            person_id=inv.qmingpian_person_id,
            img_url=file_url,
            create_name=ir_row.qmingpian_username,
        )
    except Exception as e:
        return {"error": f"企名片绑定名片失败：{e}"}
    return {"ok": True, "investor_id": inv_id, "name": inv.name}


async def _search_agency(args: dict, ctx: ToolCtx) -> dict:
    keywords = (args.get("keywords") or "").strip()
    if not keywords:
        return {"error": "keywords 不能为空"}
    items: list[dict] = []
    try:
        hits = await qmingpian_search_agency(keywords)
        for h in hits[:10]:
            name = h.get("name") or h.get("agency") or h.get("agency_name") or ""
            if not name:
                continue
            items.append({"agency_name": name, "uuid": h.get("uuid") or "", "source": "multi"})
    except Exception as e:
        logger.warning("search_agency multi failed: %s", e)
    if len(items) < 10:
        try:
            from skills.qmingpian import qmingpian_search_external_agency
            ext = await qmingpian_search_external_agency(keywords)
            seen = {it["agency_name"] for it in items}
            for raw in ext:
                name = raw if isinstance(raw, str) else (raw.get("name") if isinstance(raw, dict) else None)
                if not name or name in seen:
                    continue
                items.append({"agency_name": name, "source": "external"})
                seen.add(name)
                if len(items) >= 10:
                    break
        except Exception as e:
            logger.warning("search_agency external failed: %s", e)
    return {"ok": True, "count": len(items), "results": items}


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
        await qmingpian_add_agency_file(
            agency_name=agency, filename=filename, file_url=file_url, user_name=user_name or "",
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
    "add_agency_summary":        _add_agency_summary,
    "add_agency":                _add_agency,
    "add_agency_file":           _add_agency_file,
    "add_person_card":           _add_person_card,
}


async def dispatch(name: str, args: dict, ctx: ToolCtx) -> dict:
    fn = _DISPATCH.get(name)
    if fn is None:
        return {"error": f"direct: 未知工具 {name}"}
    return await fn(args, ctx)
