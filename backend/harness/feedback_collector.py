import time
import difflib
from sqlalchemy.ext.asyncio import AsyncSession
from models.agent_feedback import AgentFeedback

def calculate_diff_ratio(original: str, final: str) -> float:
    """0.0 = identical, 1.0 = completely different"""
    if not original:
        return 0.0
    matcher = difflib.SequenceMatcher(None, original, final)
    return round(1.0 - matcher.ratio(), 2)

async def record_feedback(
    db: AsyncSession,
    trace_id: int,
    ir_id: int,
    investor_ids: list[int],
    content_type: str,
    action: str,
    original: str,
    final: str,
    prompt_version: str,
    interrupt_time: float,
) -> AgentFeedback:
    feedback = AgentFeedback(
        trace_id=trace_id,
        ir_id=ir_id,
        investor_ids=investor_ids,
        content_type=content_type,
        action=action,
        original=original,
        final=final if action == "modified" else original,
        diff_ratio=calculate_diff_ratio(original, final) if action == "modified" else 0.0,
        prompt_version=prompt_version,
        response_time_s=int(time.time() - interrupt_time),
    )
    db.add(feedback)
    await db.commit()
    return feedback
