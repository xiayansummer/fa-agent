import json
import httpx
from harness.skill_registry import skill, skill_registry
from config import settings

BASE_URL = "https://qimingpianapi.investarget.com"


def _base(extra: dict | None = None) -> dict:
    data = {"open_id": settings.qmingpian_token}
    if extra:
        data.update(extra)
    return data


def _check(resp: httpx.Response) -> dict:
    data = resp.json()
    if str(data.get("status")) != "0":
        raise ValueError(f"企名片 API error {data.get('status')}: {data.get('message')}")
    return data


@skill(registry=skill_registry, name="企名片.查询投资人",
       version="1.0", timeout=10, retry=2, fallback=[])
async def qmingpian_search_person(keywords: str) -> list[dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BASE_URL}/Person/searchPerson",
            data=_base({"keywords": keywords}),
        )
    data = _check(resp)
    return data.get("data", {}).get("list", [])


@skill(registry=skill_registry, name="企名片.添加投资人",
       version="1.0", timeout=10, retry=1)
async def qmingpian_add_person(
    name: str,
    agency: str,
    phone: str = "",
    wechat: str = "",
    email: str = "",
    position: str = "",
    tags: list[str] | None = None,
) -> dict:
    form = _base({
        "name": name,
        "agency": agency,
    })
    if phone:
        form["phone"] = json.dumps([phone])
    if wechat:
        form["wechat"] = json.dumps([wechat])
    if email:
        form["email"] = json.dumps([email])
    if position:
        form["position"] = position
    if tags:
        form["content"] = "|".join(tags)
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{BASE_URL}/Person/addPersonInfo", data=form)
    return _check(resp).get("data", {})


@skill(registry=skill_registry, name="企名片.更新投资人",
       version="1.0", timeout=10, retry=1)
async def qmingpian_edit_person(
    person_id: str,
    name: str = "",
    agency: str = "",
    phone: str = "",
    wechat: str = "",
    email: str = "",
    position: str = "",
) -> dict:
    form = _base({"person_id": person_id})
    if name:
        form["name"] = name
    if agency:
        form["agency"] = agency
    if phone:
        form["phone"] = json.dumps([phone])
    if wechat:
        form["wechat"] = json.dumps([wechat])
    if email:
        form["email"] = json.dumps([email])
    if position:
        form["position"] = position
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{BASE_URL}/Person/editPersonInfo", data=form)
    return _check(resp).get("data", {})


@skill(registry=skill_registry, name="企名片.更新投资人标签",
       version="1.0", timeout=10, retry=1)
async def qmingpian_update_person_tags(
    name: str,
    agency: str,
    tags: list[str],
) -> dict:
    form = _base({
        "name": name,
        "agency": agency,
        "content": "|".join(tags),
    })
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{BASE_URL}/Person/updatePersonTag", data=form)
    return _check(resp).get("data", {})


@skill(registry=skill_registry, name="企名片.添加投资人纪要",
       version="1.0", timeout=10, retry=1)
async def qmingpian_add_person_summary(
    name: str,
    agency: str,
    summary: str,
    user_name: str,
) -> dict:
    form = _base({
        "name": name,
        "agency": agency,
        "summary": summary,
        "user_name": user_name,
    })
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{BASE_URL}/Summary/addPersonSummary", data=form)
    return _check(resp).get("data", {})


@skill(registry=skill_registry, name="企名片.添加机构纪要",
       version="1.0", timeout=10, retry=1)
async def qmingpian_add_agency_summary(
    agency: str,
    summary: str,
    user_name: str,
) -> dict:
    form = _base({
        "agency": agency,
        "summary": summary,
        "user_name": user_name,
    })
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{BASE_URL}/Summary/addAgencySummary", data=form)
    return _check(resp).get("data", {})


@skill(registry=skill_registry, name="企名片.查询机构",
       version="1.0", timeout=10, retry=2, fallback=[])
async def qmingpian_search_agency(keywords: str) -> list[dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BASE_URL}/Agency/searchAgency",
            data=_base({"keywords": keywords}),
        )
    data = _check(resp)
    return data.get("data", {}).get("list", [])


@skill(registry=skill_registry, name="企名片.搜索企名片机构",
       version="1.0", timeout=10, retry=2, fallback=[])
async def qmingpian_search_jigou(keywords: str) -> list[dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BASE_URL}/Search/searchJigou",
            data=_base({"keywords": keywords}),
        )
    data = _check(resp)
    return data.get("data", {}).get("list", [])


@skill(registry=skill_registry, name="企名片.导出投资人详情",
       version="1.0", timeout=15, retry=1)
async def qmingpian_export_person(person_id: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BASE_URL}/Export/exportPersonOpen",
            data=_base({"person_id": person_id}),
        )
    return _check(resp).get("data", {})


@skill(registry=skill_registry, name="企名片.导出机构详情",
       version="1.0", timeout=15, retry=1)
async def qmingpian_export_agency(jigou_id: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BASE_URL}/Export/exportAgencyOpen",
            data=_base({"jigou_id": jigou_id}),
        )
    return _check(resp).get("data", {})
