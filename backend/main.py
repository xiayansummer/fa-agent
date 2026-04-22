from fastapi import FastAPI
from auth.router import router as auth_router
from api.investors import router as investors_router
from api.calendar import router as calendar_router
from api.admin import router as admin_router
from api.agent import router as agent_router

# Load all Skills to register them into skill_registry
import skills.claude_skill  # noqa: F401
import skills.tavily_skill   # noqa: F401
import skills.qmingpian      # noqa: F401
import skills.tencent_meeting # noqa: F401
import skills.asr_skill      # noqa: F401

# Trigger workflow graph registration
import agent  # noqa: F401

app = FastAPI(title="FA Agent API", version="1.0.0")

app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(investors_router, prefix="/api/investors", tags=["investors"])
app.include_router(calendar_router, prefix="/api/calendar", tags=["calendar"])
app.include_router(admin_router, prefix="/api/admin", tags=["admin"])
app.include_router(agent_router, prefix="/api/agent", tags=["agent"])

@app.get("/health")
async def health():
    return {"status": "ok"}
