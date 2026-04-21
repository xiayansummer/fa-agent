from fastapi import FastAPI
from auth.router import router as auth_router
from api.investors import router as investors_router
from api.calendar import router as calendar_router
from api.admin import router as admin_router

app = FastAPI(title="FA Agent API", version="1.0.0")

app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(investors_router, prefix="/api/investors", tags=["investors"])
app.include_router(calendar_router, prefix="/api/calendar", tags=["calendar"])
app.include_router(admin_router, prefix="/api/admin", tags=["admin"])

@app.get("/health")
async def health():
    return {"status": "ok"}
