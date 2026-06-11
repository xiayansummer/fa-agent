from __future__ import annotations
from typing import TypedDict, Optional, Literal, Annotated
import operator

TaskType = Literal["meeting_minutes", "daily_push", "smart_list", "milestone_outreach", "briefing"]
IrAction = Literal["approved", "modified", "rejected"]


class AgentState(TypedDict):
    thread_id: str
    ir_id: int
    task_type: TaskType

    # Meeting minutes inputs
    meeting_id: Optional[str]
    audio_url: Optional[str]
    transcript: Optional[str]
    tencent_meeting_id: Optional[str]  # 新加

    # Daily push inputs
    target_date: Optional[str]   # "2026-04-22"
    events: Optional[list[dict]]

    # Smart list inputs
    criteria: Optional[str]
    candidate_ids: Optional[list[int]]
    # Smart list（方向A 机构名单）中间态：候选机构名列表 / 企名片证据文本
    agency_candidates: Optional[list]
    agency_evidence: Optional[str]

    # Milestone outreach inputs
    investor_id: Optional[int]
    milestone_type: Optional[str]  # "birthday" | "join_agency" | "first_meeting"
    ir_name: Optional[str]

    # Shared investor context (resolved from DB by first node in each workflow)
    investor_ids: Optional[list[int]]
    investor_profiles: Optional[str]

    # Orchestrator briefing intermediate state（JSON-encoded signals for synthesize node）
    briefing_signals: Optional[str]

    # daily_push raw generated messages JSON (kept separate from draft so the
    # review card shows a human-readable rendering while save_node can still
    # dispatch by investor_id from the structured payload)
    generated_messages_json: Optional[str]

    # meeting_minutes: a short 80-120 字 summary distilled from the full
    # minutes, written to InteractionLog.summary (Content Agent step).
    interaction_summary: Optional[str]

    # meeting_minutes: action items extracted from final minutes —
    # list of {title, type, due_date}. First due_date drives the
    # InteractionLog.next_followup_at; meeting_request items trigger
    # the Outreach Agent to draft an invitation message.
    action_items: Optional[list]

    # 定时任务（无人工审核）直接生成草稿落库时置 True，save_node 据此走
    # per-investor 分发并以 status=draft 保存，不进 review 中断。
    auto_draft: Optional[bool]

    # Output
    draft: Optional[str]
    final: Optional[str]
    ir_action: Optional[IrAction]

    # Trace metadata
    prompt_version: Optional[str]
    skills_called: Annotated[list[str], operator.add]
    error: Optional[str]
