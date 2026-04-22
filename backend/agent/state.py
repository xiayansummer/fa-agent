from __future__ import annotations
from typing import TypedDict, Optional, Literal, Annotated
import operator

TaskType = Literal["meeting_minutes", "daily_push", "smart_list", "milestone_outreach"]
IrAction = Literal["approved", "modified", "rejected"]


class AgentState(TypedDict):
    thread_id: str
    ir_id: int
    task_type: TaskType

    # Meeting minutes inputs
    meeting_id: Optional[str]
    audio_url: Optional[str]
    transcript: Optional[str]

    # Daily push inputs
    target_date: Optional[str]   # "2026-04-22"
    events: Optional[list[dict]]

    # Smart list inputs
    criteria: Optional[str]
    candidate_ids: Optional[list[int]]

    # Milestone outreach inputs
    investor_id: Optional[int]
    milestone_type: Optional[str]  # "birthday" | "join_agency" | "first_meeting"
    ir_name: Optional[str]

    # Shared investor context (resolved from DB by first node in each workflow)
    investor_ids: Optional[list[int]]
    investor_profiles: Optional[str]

    # Output
    draft: Optional[str]
    final: Optional[str]
    ir_action: Optional[IrAction]

    # Trace metadata
    prompt_version: Optional[str]
    skills_called: Annotated[list[str], operator.add]
    error: Optional[str]
