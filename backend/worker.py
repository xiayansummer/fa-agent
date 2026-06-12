import os
import sys

# Celery prefork 子进程默认 sys.path 不含 backend 目录，导致 task 里
# from agent.xxx 报 ModuleNotFoundError。worker.py 这里强制加进去。
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from celery import Celery
from celery.schedules import crontab
from config import settings

# Celery worker 子进程不会自动 import skills 模块 —— 必须在 worker.py 主入口
# 显式导入，让 @skill 装饰器注册到 skill_registry。fastapi 进程在 main.py
# 做的事，celery 这边也要做一遍。
import skills.claude_skill   # noqa: F401
import skills.tavily_skill   # noqa: F401
import skills.qmingpian      # noqa: F401
import skills.tencent_meeting  # noqa: F401
import skills.asr_skill      # noqa: F401
import skills.doc_extract    # noqa: F401

# 定时任务直接在 worker 进程内跑 workflow 节点（见 agent/scheduled.py），
# 不再 HTTP 自调 FastAPI（鉴权+端口都不通，曾长期 405 失败）。
import agent.workflows.daily_push          # noqa: F401
import agent.workflows.milestone_outreach  # noqa: F401

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
    "schedule-reminders-5min": {
        "task": "worker.trigger_schedule_reminders",
        "schedule": 300.0,  # 每 5 分钟；任务内部用 Asia/Shanghai 算时间，不受容器 UTC 影响
    },
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
    """每个活跃 IR 在其投资人范围内生成 daily_push 草稿（进程内跑，无人工审核）。"""
    import asyncio, os, sys
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    from agent.scheduled import run_daily_push_for_all_irs
    try:
        return asyncio.run(run_daily_push_for_all_irs())
    except Exception as exc:
        raise self.retry(exc=exc, countdown=300, max_retries=3)


@celery_app.task(name="worker.dispatch_outreach", bind=True, max_retries=2)
def dispatch_outreach(self, ir_id: int, investor_ids: list,
                      action_items: list, summary: str):
    """异步执行 outreach 草稿生成 —— 把会议纪要 workflow 的 dispatch_outreach 节点
    从主路径剥离，让 review approved 后立即 done，draft 在后台慢慢生成。"""
    import asyncio, os, sys
    # prefork 子进程 sys.path 不含 backend，task 执行点 explicit 加
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
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


@celery_app.task(name="worker.trigger_schedule_reminders", bind=True)
def trigger_schedule_reminders(self):
    """日程订阅消息提醒：30 分钟窗口内开始的 calendar_events → 微信服务通知。"""
    import asyncio, os, sys
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    from agent.scheduled import run_schedule_reminders
    try:
        return asyncio.run(run_schedule_reminders())
    except Exception as exc:
        # 提醒任务高频跑，失败不重试（下个 5 分钟自然再试），只记日志
        import logging
        logging.getLogger(__name__).exception("schedule reminders tick failed")
        return {"error": str(exc)}


@celery_app.task(name="worker.trigger_milestone_outreach", bind=True)
def trigger_milestone_outreach(self):
    """每个活跃 IR 的投资人今日生日/入职纪念日 → 生成 milestone 草稿（进程内跑，无人工审核）。"""
    import asyncio, os, sys
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    from agent.scheduled import run_milestone_outreach_for_all_irs
    try:
        return asyncio.run(run_milestone_outreach_for_all_irs())
    except Exception as exc:
        raise self.retry(exc=exc, countdown=300, max_retries=3)
