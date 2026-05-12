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


@skill(registry=skill_registry, name="企名片.设置投资人熟悉度",
       version="1.0", timeout=10, retry=1)
async def qmingpian_add_familiar_person(
    name: str,
    agency: str,
    user_name: str,
    level: str,
) -> dict:
    """设置某 IR 对某投资人的熟悉度。
    - name/agency: 投资人姓名+机构
    - user_name: IR 在企名片系统内的用户名（如 'Investarget'）
    - level: 熟悉度等级（必须是企名片预配置的值，如"加过微信"/"见过面"/...）
    """
    form = _base({
        "name": name,
        "agency": agency,
        "user_name": user_name,
        "level": level,
    })
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{BASE_URL}/Person/addFamiliarPerson", data=form)
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
    """通过姓名导出投资人详情（xlsx）。

    返回包含 3 个 sheet 的 xlsx，解析后返回：
    - agency / phone / email / industry —— 来自"投资人详情"sheet
    - summaries: list of {content, creator, created_at} —— 来自"投资人纪要"sheet
    - history: list of {event, agency, industry, round, status, feedback, contact_time}
      —— 来自"历史推荐"sheet

    只能查到当前 open_id 范围内的投资人（自己加过的或被共享的）。
    """
    import io
    from openpyxl import load_workbook

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{BASE_URL}/Export/exportPersonOpen",
            data=_base({"person_name": person_name}),
        )

    ct = resp.headers.get("content-type", "")
    if not ct.startswith("application/vnd.openxmlformats"):
        try:
            d = resp.json()
            raise ValueError(f"企名片 export error: status={d.get('status')} message={d.get('message')}")
        except Exception:
            raise ValueError(f"企名片 export 失败，返回非 xlsx: {ct}")

    wb = load_workbook(io.BytesIO(resp.content), read_only=True)
    result: dict = {"summaries": [], "history": []}

    for sn in wb.sheetnames:
        ws = wb[sn]
        rows = list(ws.iter_rows(values_only=True))
        if len(rows) < 3:
            continue
        headers = [str(c).strip() if c else "" for c in rows[1]]

        if sn == "投资人详情":
            values = rows[2]
            for h, v in zip(headers, values):
                if not h or v in (None, ""):
                    continue
                if h == "机构":
                    result["agency"] = v
                elif h == "手机":
                    result["phone"] = [str(v)]
                elif h == "邮箱":
                    result["email"] = [str(v)]
                elif h in ("FAwork行业", "行业"):
                    result["industry"] = str(v)

        elif sn == "投资人纪要":
            # 表头: 纪要内容 / 创建人 / 创建时间
            for row in rows[2:]:
                if not row or all(c in (None, "") for c in row):
                    continue
                result["summaries"].append({
                    "content": str(row[0]) if len(row) > 0 and row[0] else "",
                    "creator": str(row[1]) if len(row) > 1 and row[1] else "",
                    "created_at": str(row[2]) if len(row) > 2 and row[2] else "",
                })

        elif sn == "历史推荐":
            # 表头: 事件名 / 机构 / 行业 / 服务轮次 / 状态 / 反馈及进展 / 对接时间
            for row in rows[2:]:
                if not row or all(c in (None, "") for c in row):
                    continue
                result["history"].append({
                    "event": str(row[0]) if len(row) > 0 and row[0] else "",
                    "agency": str(row[1]) if len(row) > 1 and row[1] else "",
                    "industry": str(row[2]) if len(row) > 2 and row[2] else "",
                    "round": str(row[3]) if len(row) > 3 and row[3] else "",
                    "status": str(row[4]) if len(row) > 4 and row[4] else "",
                    "feedback": str(row[5]) if len(row) > 5 and row[5] else "",
                    "contact_time": str(row[6]) if len(row) > 6 and row[6] else "",
                })

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
