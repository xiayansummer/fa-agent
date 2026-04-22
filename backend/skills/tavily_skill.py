import httpx
from harness.skill_registry import skill, skill_registry
from config import settings

@skill(registry=skill_registry, name="Tavily.搜索",
       version="1.0", timeout=10, retry=2,
       fallback=[])
async def tavily_search(query: str, max_results: int = 5) -> list[dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.tavily.com/search",
            json={"api_key": settings.tavily_api_key,
                  "query": query,
                  "max_results": max_results,
                  "search_depth": "basic"},
        )
    data = resp.json()
    return [{"title": r["title"], "content": r["content"], "url": r["url"]}
            for r in data.get("results", [])]
