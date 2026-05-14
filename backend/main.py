from fastapi import FastAPI
from auth.router import router as auth_router
from api.investors import router as investors_router
from api.calendar import router as calendar_router
from api.admin import router as admin_router
from api.agent import router as agent_router
from api.upload import router as upload_router
from api.me import router as me_router
from api.interactions import router as interactions_router
from api.outreach import router as outreach_router

# Load all Skills to register them into skill_registry
import skills.claude_skill  # noqa: F401
import skills.tavily_skill   # noqa: F401
import skills.qmingpian      # noqa: F401
import skills.tencent_meeting # noqa: F401
import skills.asr_skill      # noqa: F401

# Trigger workflow graph registration
import agent  # noqa: F401

from agent.runner import setup_checkpointer

app = FastAPI(title="FA Agent API", version="1.0.0")


@app.on_event("startup")
async def _init_checkpointer() -> None:
    await setup_checkpointer()

app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(investors_router, prefix="/api/investors", tags=["investors"])
app.include_router(calendar_router, prefix="/api/calendar", tags=["calendar"])
app.include_router(admin_router, prefix="/api/admin", tags=["admin"])
app.include_router(agent_router, prefix="/api/agent", tags=["agent"])
app.include_router(upload_router, prefix="/api/upload", tags=["upload"])
app.include_router(me_router, prefix="/api/me", tags=["me"])
app.include_router(interactions_router, prefix="/api/investors", tags=["interactions"])
app.include_router(outreach_router, prefix="/api/outreach", tags=["outreach"])

@app.get("/health")
async def health():
    return {"status": "ok"}
