from __future__ import annotations
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
async def qmingpian_export_person(person_name: str) -> dict:
    """通过姓名查询投资人详情。

    注意：该接口返回的是 xlsx 文件（非 JSON），表头：机构/手机/邮箱/FAwork行业。
    只能查到当前 open_id 范围内的投资人（自己加过的或被共享的）。
    """
    import io
    from openpyxl import load_workbook

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{BASE_URL}/Export/exportPersonOpen",
            data=_base({"person_name": person_name}),
        )

    # 如果 status code 异常或返回 JSON（说明出错）
    ct = resp.headers.get("content-type", "")
    if not ct.startswith("application/vnd.openxmlformats"):
        # 出错时是 JSON
        try:
            d = resp.json()
            raise ValueError(f"企名片 export error: status={d.get('status')} message={d.get('message')}")
        except Exception:
            raise ValueError(f"企名片 export 失败，返回非 xlsx: {ct}")

    wb = load_workbook(io.BytesIO(resp.content), read_only=True)
    sheet = wb[wb.sheetnames[0]]
    rows = list(sheet.iter_rows(values_only=True))

    # row 0: 标题行（如"投资人详情"）； row 1: 表头； row 2: 数据
    if len(rows) < 3:
        return {}
    headers = [str(c).strip() if c else "" for c in rows[1]]
    values = rows[2]

    result: dict = {}
    for h, v in zip(headers, values):
        if not h or v in (None, ""):
            continue
        # 映射企名片表头 → 我们的字段名
        if h == "机构":
            result["agency"] = v
        elif h == "手机":
            result["phone"] = [str(v)]
        elif h == "邮箱":
            result["email"] = [str(v)]
        elif h == "FAwork行业" or h == "行业":
            result["industry"] = str(v)
    return result


@skill(registry=skill_registry, name="企名片.导出机构详情",
       version="1.0", timeout=15, retry=1)
async def qmingpian_export_agency(jigou_id: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BASE_URL}/Export/exportAgencyOpen",
            data=_base({"jigou_id": jigou_id}),
        )
    return _check(resp).get("data", {})
