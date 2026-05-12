from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from typing import Optional
from datetime import datetime
from datetime import date as date_type
from pydantic import BaseModel
from database import get_db
from models.investors import Investor
from auth.jwt import get_current_ir
from skills.qmingpian import (
    qmingpian_search_person,
    qmingpian_add_person,
    qmingpian_edit_person,
    qmingpian_export_person,
)

router = APIRouter()


class InvestorOut(BaseModel):
    id: int
    qmingpian_person_id: Optional[str] = None
    name: str
    agency: Optional[str] = None
    position: Optional[str] = None
    avatar_url: Optional[str] = None
    business_card_url: Optional[str] = None
    familiarity: Optional[str] = None
    industry_tags: Optional[list] = None
    stage_pref: Optional[list] = None
    relationship_score: int = 0
    profile_notes: Optional[str] = None
    last_interaction_at: Optional[datetime] = None
    birthday: Optional[date_type] = None

    model_config = {"from_attributes": True}


class SearchHitOut(BaseModel):
    """搜索结果条目：可能是本地已有的投资人（local_id 非 None），也可能仅在企名片存在。"""
    qmingpian_person_id: str
    name: str
    agency: Optional[str] = None
    position: Optional[str] = None       # 企名片 zhiwu
    local_id: Optional[int] = None       # 本地 fa_agent.investors.id；None 表示尚未加入本地库
    avatar_url: Optional[str] = None     # 企名片 icon 或 本地 avatar_url
    business_card_url: Optional[str] = None  # 企名片 url 或 本地 business_card_url


class InvestorListOut(BaseModel):
    items: list[InvestorOut]
    total: int


class SearchListOut(BaseModel):
    items: list[SearchHitOut]
    total: int


class InvestorCreate(BaseModel):
    # 企名片必填
    name: str
    agency: Optional[str] = ""  # 企名片 addPerson 要求 agency 必传，空字符串也可
    # 企名片可选基本信息
    position: Optional[str] = None
    email: Optional[list] = None
    wechat: Optional[list] = None
    phone: Optional[list] = None
    # 仅本地的业务画像
    avatar_url: Optional[str] = None
    business_card_url: Optional[str] = None
    familiarity: Optional[str] = None
    industry_tags: Optional[list] = None
    stage_pref: Optional[list] = None
    quota_range: Optional[str] = None
    relationship_score: int = 0
    profile_notes: Optional[str] = None
    birthday: Optional[date_type] = None
    join_agency_date: Optional[date_type] = None
    first_meeting_date: Optional[date_type] = None
    # 如果用户从搜索结果"加入我的库"，传 person_id 跳过 addPerson
    qmingpian_person_id: Optional[str] = None


class InvestorUpdate(BaseModel):
    # 企名片同步字段
    name: Optional[str] = None
    agency: Optional[str] = None
    position: Optional[str] = None
    email: Optional[list] = None
    wechat: Optional[list] = None
    phone: Optional[list] = None
    # 仅本地字段
    avatar_url: Optional[str] = None
    business_card_url: Optional[str] = None
    familiarity: Optional[str] = None
    industry_tags: Optional[list] = None
    stage_pref: Optional[list] = None
    quota_range: Optional[str] = None
    relationship_score: Optional[int] = None
    profile_notes: Optional[str] = None
    birthday: Optional[date_type] = None
    join_agency_date: Optional[date_type] = None
    first_meeting_date: Optional[date_type] = None


def _first_or_empty(lst: Optional[list]) -> str:
    if not lst:
        return ""
    return str(lst[0]) if lst[0] is not None else ""


_QMINGPIAN_FIELDS = {"name", "agency", "position", "email", "wechat", "phone"}


@router.get("", response_model=InvestorListOut)
async def list_investors(
    stage: Optional[str] = Query(None, description="阶段筛选，匹配 stage_pref"),
    industry: Optional[str] = Query(None, description="行业筛选，匹配 industry_tags"),
    limit: Optional[int] = Query(None, ge=1, le=1000, description="可选限制返回条数；不传则全部"),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_ir),
):
    """
    "我的库" 视图：本地 investors 表里 is_active=true 的投资人，按 last_interaction_at 倒序。
    默认返回全部；可选 limit 限制条数。搜索请用 /api/investors/search?q=。
    """
    stmt = select(Investor).where(Investor.is_active == True)
    if stage:
        stmt = stmt.where(Investor.stage_pref.contains(f'"{stage}"'))
    if industry:
        stmt = stmt.where(Investor.industry_tags.contains(f'"{industry}"'))
    # MySQL 不支持 NULLS LAST，用 (col IS NULL) 排序实现：非空在前，再按 desc
    stmt = stmt.order_by(
        Investor.last_interaction_at.is_(None).asc(),
        Investor.last_interaction_at.desc(),
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    investors = result.scalars().all()
    return InvestorListOut(items=list(investors), total=len(investors))


@router.get("/search", response_model=SearchListOut)
async def search_investors(
    q: str = Query(..., min_length=1, description="搜索关键字（企名片全库）"),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_ir),
):
    """
    在企名片全库搜索。返回结果中标注哪些已加入本地库（local_id 非 null）。
    点击未加入的条目时，前端调 POST /api/investors { qmingpian_person_id } 加入本地。
    """
    try:
        hits = await qmingpian_search_person(q)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"企名片搜索失败: {e}")

    # 查本地 investors 表里这些 person_id 的本地 id + avatar + 名片
    person_ids = [h.get("person_id") for h in hits if h.get("person_id")]
    local_map: dict[str, dict] = {}
    if person_ids:
        local_result = await db.execute(
            select(
                Investor.id,
                Investor.qmingpian_person_id,
                Investor.avatar_url,
                Investor.business_card_url,
            )
            .where(Investor.qmingpian_person_id.in_(person_ids))
            .where(Investor.is_active == True)
        )
        for row in local_result.all():
            local_map[row.qmingpian_person_id] = {
                "id": row.id,
                "avatar_url": row.avatar_url,
                "business_card_url": row.business_card_url,
            }

    items = []
    for h in hits:
        pid = h.get("person_id")
        if not pid:
            continue
        local_info = local_map.get(pid)
        # 本地已有的优先用本地（IR 可能上传过更新的）；否则用企名片返回
        qmingpian_icon = h.get("icon") or None
        qmingpian_card = h.get("url") or None
        items.append(SearchHitOut(
            qmingpian_person_id=pid,
            name=h.get("name", ""),
            agency=h.get("agency"),
            position=h.get("zhiwu") or None,
            local_id=local_info["id"] if local_info else None,
            avatar_url=(local_info["avatar_url"] if local_info and local_info["avatar_url"]
                       else qmingpian_icon),
            business_card_url=(local_info["business_card_url"] if local_info and local_info["business_card_url"]
                              else qmingpian_card),
        ))
    return SearchListOut(items=items, total=len(items))


class EnrichedQmingpianOut(BaseModel):
    """从企名片综合拉取的投资人详情。组合了：
    - searchPerson: position(zhiwu) / avatar_url(icon) / business_card_url(url)
    - exportPersonOpen xlsx: agency / phone / email / industry(FAwork行业)
    """
    agency: Optional[str] = None
    position: Optional[str] = None
    avatar_url: Optional[str] = None
    business_card_url: Optional[str] = None
    phone: Optional[list] = None
    email: Optional[list] = None
    industry: Optional[str] = None


@router.get("/qmingpian/by-name", response_model=EnrichedQmingpianOut)
async def enrich_from_qmingpian(
    person_name: str = Query(..., min_length=1, description="投资人姓名"),
    _: dict = Depends(get_current_ir),
):
    """按姓名从企名片综合拉详情（基本信息 + 头像/名片图 + 手机邮箱）。
    查不到时返 200 + 空字段，前端 graceful。"""
    out = EnrichedQmingpianOut()
    # 1) searchPerson 拿头像/名片/职位/机构
    try:
        hits = await qmingpian_search_person(person_name)
        if hits:
            # 精确匹配姓名优先；没有就取第一条
            hit = next((h for h in hits if h.get("name") == person_name), hits[0])
            out.agency = hit.get("agency") or None
            out.position = hit.get("zhiwu") or None
            out.avatar_url = hit.get("icon") or None
            out.business_card_url = hit.get("url") or None
    except Exception:
        pass
    # 2) exportPersonOpen 拉 phone/email（xlsx 解析）
    try:
        ex = await qmingpian_export_person(person_name)
        if ex and isinstance(ex, dict):
            if not out.agency and ex.get("agency"):
                out.agency = ex.get("agency")
            out.phone = ex.get("phone")
            out.email = ex.get("email")
            out.industry = ex.get("industry")
    except Exception:
        pass
    return out


@router.get("/{investor_id}", response_model=InvestorOut)
async def get_investor(
    investor_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_ir),
):
    result = await db.execute(select(Investor).where(Investor.id == investor_id))
    investor = result.scalar_one_or_none()
    if not investor:
        raise HTTPException(status_code=404, detail="投资人不存在")
    return investor


@router.post("", response_model=InvestorOut)
async def create_investor(
    body: InvestorCreate,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_ir),
):
    """
    新增：
    - 若传了 qmingpian_person_id（来自搜索结果），跳过 addPerson，直接本地建关联记录；
    - 否则先调企名片 addPerson 拿 person_id，再本地建。
    重名（已有同 person_id 的本地记录）→ 400。
    """
    person_id = body.qmingpian_person_id

    if person_id:
        # 检查本地是否已有
        existing = await db.execute(
            select(Investor).where(Investor.qmingpian_person_id == person_id)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="该投资人已在你的库中")
    else:
        # 调企名片新增
        try:
            res = await qmingpian_add_person(
                name=body.name,
                agency=body.agency or "",
                phone=_first_or_empty(body.phone),
                wechat=_first_or_empty(body.wechat),
                email=_first_or_empty(body.email),
                position=body.position or "",
            )
            person_id = res.get("person_id")
            if not person_id:
                raise HTTPException(status_code=502, detail=f"企名片未返回 person_id: {res}")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"企名片新增失败: {e}")

    # 本地插入（含扩展字段）
    investor = Investor(
        qmingpian_person_id=person_id,
        name=body.name,
        agency=body.agency,
        position=body.position,
        email=body.email,
        wechat=body.wechat,
        phone=body.phone,
        avatar_url=body.avatar_url,
        business_card_url=body.business_card_url,
        familiarity=body.familiarity,
        industry_tags=body.industry_tags,
        stage_pref=body.stage_pref,
        quota_range=body.quota_range,
        relationship_score=body.relationship_score,
        profile_notes=body.profile_notes,
        birthday=body.birthday,
        join_agency_date=body.join_agency_date,
        first_meeting_date=body.first_meeting_date,
    )
    db.add(investor)
    await db.commit()
    await db.refresh(investor)
    return investor


@router.put("/{investor_id}", response_model=InvestorOut)
async def update_investor(
    investor_id: int,
    body: InvestorUpdate,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_ir),
):
    """
    编辑：
    - 同步基本信息（name/agency/position/phone/wechat/email）到企名片（若有 person_id）;
    - 扩展字段（关系值/标签/备注/生日等）写本地。
    """
    result = await db.execute(select(Investor).where(Investor.id == investor_id))
    investor = result.scalar_one_or_none()
    if not investor:
        raise HTTPException(status_code=404, detail="投资人不存在")

    updates = body.model_dump(exclude_unset=True)

    # 1) 同步基本信息到企名片
    qmingpian_changes = {k: v for k, v in updates.items() if k in _QMINGPIAN_FIELDS}
    if qmingpian_changes and investor.qmingpian_person_id:
        try:
            await qmingpian_edit_person(
                person_id=investor.qmingpian_person_id,
                name=qmingpian_changes.get("name") or "",
                agency=qmingpian_changes.get("agency") or "",
                phone=_first_or_empty(qmingpian_changes.get("phone")),
                wechat=_first_or_empty(qmingpian_changes.get("wechat")),
                email=_first_or_empty(qmingpian_changes.get("email")),
                position=qmingpian_changes.get("position") or "",
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"企名片更新失败: {e}")

    # 2) 写本地（全部字段，包括 qmingpian 同步过的，保持镜像）
    for field, value in updates.items():
        setattr(investor, field, value)
    await db.commit()
    await db.refresh(investor)
    return investor


@router.delete("/{investor_id}")
async def delete_investor(
    investor_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_ir),
):
    """
    软删除（仅本地隐藏，企名片记录不动）。
    """
    result = await db.execute(select(Investor).where(Investor.id == investor_id))
    investor = result.scalar_one_or_none()
    if not investor or not investor.is_active:
        raise HTTPException(status_code=404, detail="投资人不存在或已删除")
    investor.is_active = False
    await db.commit()
    return {"deleted": True}
