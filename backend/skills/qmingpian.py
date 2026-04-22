import httpx
from harness.skill_registry import skill, skill_registry
from config import settings

def _headers():
    return {"Authorization": f"Bearer {settings.qmingpian_token}",
            "Content-Type": "application/json"}

@skill(registry=skill_registry, name="企名片.查询投资人",
       version="1.0", timeout=8, retry=2, fallback=[])
async def qmingpian_search(query: str) -> list[dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.qmingpian_api_url}/investor/search",
            headers=_headers(),
            json={"keyword": query, "page": 1, "pageSize": 20},
        )
    return resp.json().get("data", {}).get("list", [])

@skill(registry=skill_registry, name="企名片.新增投资人",
       version="1.0", timeout=8, retry=1)
async def qmingpian_add(investor_data: dict) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.qmingpian_api_url}/investor/add",
            headers=_headers(),
            json=investor_data,
        )
    return resp.json().get("data", {}).get("id", "")

@skill(registry=skill_registry, name="企名片.更新标签",
       version="1.0", timeout=8, retry=1)
async def qmingpian_update_tags(investor_id: str, tags: list[str]) -> bool:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.qmingpian_api_url}/investor/tag/update",
            headers=_headers(),
            json={"id": investor_id, "tags": tags},
        )
    return resp.json().get("code") == 0

@skill(registry=skill_registry, name="企名片.添加备注",
       version="1.0", timeout=8, retry=1)
async def qmingpian_add_note(investor_id: str, note: str) -> bool:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.qmingpian_api_url}/investor/note/add",
            headers=_headers(),
            json={"id": investor_id, "content": note},
        )
    return resp.json().get("code") == 0

@skill(registry=skill_registry, name="企名片.发送消息",
       version="1.0", timeout=10, retry=1)
async def qmingpian_send_message(investor_id: str, content: str) -> bool:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.qmingpian_api_url}/message/send",
            headers=_headers(),
            json={"investorId": investor_id, "content": content},
        )
    return resp.json().get("code") == 0
