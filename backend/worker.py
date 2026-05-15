from celery import Celery
from celery.schedules import crontab
from config import settings

_API_BASE = settings.internal_api_base

celery_app = Celery(
    "fa_agent",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["worker"],
)

celery_app.conf.task_routes = {
    "worker.trigger_daily_push": {"queue": "content"},
    "worker.trigger_milestone_outreach": {"queue": "content"},
    "worker.dispatch_outreach": {"queue": "content"},
}

celery_app.conf.beat_schedule = {
    "daily-push-9am": {
        "task": "worker.trigger_daily_push",
        "schedule": crontab(hour=9, minute=0),
    },
    "milestone-check-8am": {
        "task": "worker.trigger_milestone_outreach",
        "schedule": crontab(hour=8, minute=0),
    },
}


@celery_app.task(name="worker.trigger_daily_push", bind=True)
def trigger_daily_push(self):
    """Kick off daily push workflow via internal HTTP call to FastAPI."""
    import httpx
    from datetime import date

    try:
        resp = httpx.post(
            f"{_API_BASE}/api/agent/run",
            json={
                "task_type": "daily_push",
                "target_date": date.today().isoformat(),
            },
            headers={"X-Celery-Internal": "1"},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as exc:
        raise self.retry(exc=exc, countdown=300, max_retries=3)


@celery_app.task(name="worker.dispatch_outreach", bind=True, max_retries=2)
def dispatch_outreach(self, ir_id: int, investor_ids: list,
                      action_items: list, summary: str):
    """异步执行 outreach 草稿生成 —— 把会议纪要 workflow 的 dispatch_outreach 节点
    从主路径剥离，让 review approved 后立即 done，draft 在后台慢慢生成。"""
    import asyncio
    from agent.dispatch_outreach import dispatch_outreach_impl
    try:
        return asyncio.run(dispatch_outreach_impl(
            ir_id=int(ir_id),
            investor_ids=list(investor_ids or []),
            action_items=list(action_items or []),
            summary=summary or "",
        ))
    except Exception as exc:
        raise self.retry(exc=exc, countdown=120)


@celery_app.task(name="worker.trigger_milestone_outreach", bind=True)
def trigger_milestone_outreach(self):
    """Check today's milestones and trigger outreach workflow for each."""
    import httpx
    from datetime import date

    today = date.today()
    try:
        resp = httpx.get(
            f"{_API_BASE}/api/calendar/daily",
            params={"date": today.isoformat()},
            headers={"X-Celery-Internal": "1"},
            timeout=10,
        )
        resp.raise_for_status()
        events = resp.json().get("events", [])
        for event in events:
            if event.get("type") in ("birthday", "join_agency"):
                httpx.post(
                    f"{_API_BASE}/api/agent/run",
                    json={
                        "task_type": "milestone_outreach",
                        "investor_id": event["investor_id"],
                        "milestone_type": event["type"],
                        "ir_name": "IR",
                    },
                    headers={"X-Celery-Internal": "1"},
                    timeout=10,
                )
    except Exception as exc:
        raise self.retry(exc=exc, countdown=300, max_retries=3)
