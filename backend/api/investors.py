from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from typing import Optional
from datetime import datetime
from datetime import date as date_type
from pydantic import BaseModel
from database import get_db
from models.investors import Investor
from auth.jwt import get_current_ir
from models.ir_users import IRUser
from skills.qmingpian import (
    qmingpian_search_person,
    qmingpian_add_person,
    qmingpian_edit_person,
    qmingpian_export_person,
    qmingpian_add_familiar_person,
    qmingpian_update_familiar_person,
    qmingpian_update_person_tags,
    qmingpian_upload_file,
    qmingpian_add_person_card,
)
import logging

logger = logging.getLogger(__name__)

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
    # 仅在 POST/PUT 响应里有值：企名片同步过程中失败的项，前端可 toast
    qmingpian_warnings: list[str] = []

    model_config = {"from_attributes": True}


class SearchHitOut(BaseModel):
    """搜索结果条目：可能是本地已有的投资人（local_id 非 None），也可能仅在企名片存在。

    avatar_url / business_card_url 优先用本地已上传的；本地无则用企名片返回的（icon/url 字段）。
    position / tags / industries 来自企名片 searchPerson 返回的 zhiwu / tag / industry_info。
    """
    qmingpian_person_id: str
    name: str
    agency: Optional[str] = None
    local_id: Optional[int] = None
    avatar_url: Optional[str] = None       # 本地 avatar_url 或企名片 icon
    business_card_url: Optional[str] = None  # 本地 business_card_url 或企名片 url
    position: Optional[str] = None           # 来自企名片 zhiwu
    tags: list[str] = []                     # 来自企名片 tag 字段（"|" 拆分）
    industries: list[str] = []               # 来自企名片 industry_info 一级行业去重


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
    # 企名片扩展字段（仅在 POST 创建时透传给 addPersonInfo，本地不存）
    gender: Optional[str] = None              # '男' / '女'
    level: Optional[str] = None               # '高' / '低'（投资人级别，与 familiarity 是两个概念）
    office_location: Optional[str] = None
    introduction: Optional[str] = None
    is_dimission: Optional[int] = None        # 1=离职 0=在职
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
    # 投资人标签：写回企名片 updatePersonTag (本地不存)
    qmingpian_tags: Optional[list[str]] = None


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
    # 投资人标签：写回企名片 updatePersonTag (本地不存)
    qmingpian_tags: Optional[list[str]] = None


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

    # 新鉴权下 searchPerson 可能为同一 person_id 返回多条（每张名片一条）。
    # 按 person_id 去重 + 聚合所有 url。第一次出现的条目作为字段主源。
    seen_pids: set[str] = set()
    items = []
    for h in hits:
        pid = h.get("person_id")
        if not pid or pid in seen_pids:
            continue
        seen_pids.add(pid)
        local_info = local_map.get(pid)
        # 本地有则用本地，否则用企名片返回的 icon (头像) / url (名片图)
        qm_icon = h.get("icon") or None
        qm_card = h.get("url") or None
        # 标签
        raw_tag = h.get("tag") or ""
        tags = [t.strip() for t in raw_tag.split("|") if t and t.strip()]
        # 关注行业
        industries: list[str] = []
        seen: set[str] = set()
        for it in (h.get("industry_info") or []):
            ind = (it or {}).get("industry")
            if ind and ind not in seen:
                industries.append(ind)
                seen.add(ind)
        items.append(SearchHitOut(
            qmingpian_person_id=pid,
            name=h.get("name", ""),
            agency=h.get("agency"),
            local_id=local_info["id"] if local_info else None,
            avatar_url=(local_info["avatar_url"] if local_info else None) or qm_icon,
            business_card_url=(local_info["business_card_url"] if local_info else None) or qm_card,
            position=h.get("zhiwu") or None,
            tags=tags,
            industries=industries,
        ))
    return SearchListOut(items=items, total=len(items))


class QmingpianSummary(BaseModel):
    content: str
    creator: Optional[str] = None
    created_at: Optional[str] = None


class QmingpianHistory(BaseModel):
    event: str
    agency: Optional[str] = None
    industry: Optional[str] = None
    round: Optional[str] = None
    status: Optional[str] = None
    feedback: Optional[str] = None
    contact_time: Optional[str] = None


class QmingpianFamiliarPerson(BaseModel):
    """团队里某个 IR 对投资人的熟悉度（来自企名片"熟悉人"sheet）。
    name 是该 IR 在企名片的用户名（如 'Investarget'）。"""
    name: str
    level: str


class EnrichedQmingpianOut(BaseModel):
    """从企名片 exportPersonOpen 拉取的投资人详情（xlsx 解析，4 个 sheet）。"""
    agency: Optional[str] = None
    phone: Optional[list] = None
    email: Optional[list] = None
    industry: Optional[str] = None
    summaries: list[QmingpianSummary] = []
    history: list[QmingpianHistory] = []
    familiar_persons: list[QmingpianFamiliarPerson] = []


class QmingpianHitOut(BaseModel):
    """企名片单条快照：编辑页用，标签 + 关注行业 + 职务（来自 searchPerson）。
    cards 包含同 person_id 所有名片 url（企名片新鉴权下 list 按名片维度返回，
    同 person_id 会重复多次，每条对应一张名片）。"""
    person_id: Optional[str] = None
    position: Optional[str] = None
    tags: list[str] = []
    industries: list[str] = []
    cards: list[str] = []   # 所有名片 url


@router.get("/qmingpian/searchhit", response_model=QmingpianHitOut)
async def qmingpian_hit(
    name: str = Query(..., min_length=1, description="投资人姓名"),
    agency: Optional[str] = Query(None, description="机构名（用于在多结果时精确定位）"),
    _: dict = Depends(get_current_ir),
):
    """编辑页拉企名片单条快照（标签、关注行业、职务、所有名片）。
    用 name 调 searchPerson，agency 用于在多结果时精确匹配；cards 按 person_id 聚合 url。"""
    try:
        hits = await qmingpian_search_person(name)
    except Exception:
        return QmingpianHitOut()
    if not hits:
        return QmingpianHitOut()

    pick = None
    if agency:
        for h in hits:
            if (h.get("agency") or "") == agency and (h.get("name") or "") == name:
                pick = h
                break
    if pick is None:
        # 兜底：取第一条同名的
        for h in hits:
            if (h.get("name") or "") == name:
                pick = h
                break
    if pick is None:
        return QmingpianHitOut()

    # 聚合同 person_id 的所有名片 url，按时间倒序（最新在 cards[0]）。
    # 时间从 URL 内嵌的时间戳解析：
    #   image.investarget.com/<14位YYYYMMDDHHMMSS>...    （历史域名）
    #   qimingpianfile.../userUpload/file_<8位hex>...    （新上传：hex 时间戳）
    import re
    from datetime import datetime
    _RE_OLD = re.compile(r"/(\d{14})")
    _RE_NEW = re.compile(r"/file_([0-9a-fA-F]{8})")

    def _card_ts(url: str) -> int:
        m = _RE_OLD.search(url)
        if m:
            try:
                return int(datetime.strptime(m.group(1), "%Y%m%d%H%M%S").timestamp())
            except ValueError:
                pass
        m = _RE_NEW.search(url)
        if m:
            try:
                return int(m.group(1), 16)
            except ValueError:
                pass
        return 0  # 无法解析 → 排最后

    target_pid = pick.get("person_id")
    cards_seen: set[str] = set()
    raw_cards: list[str] = []
    for h in hits:
        if h.get("person_id") != target_pid:
            continue
        u = (h.get("url") or "").strip()
        if u and u not in cards_seen:
            raw_cards.append(u)
            cards_seen.add(u)
    cards = sorted(raw_cards, key=_card_ts, reverse=True)

    raw_tag = pick.get("tag") or ""
    tags = [t.strip() for t in raw_tag.split("|") if t and t.strip()]
    industries: list[str] = []
    seen: set[str] = set()
    for it in (pick.get("industry_info") or []):
        ind = (it or {}).get("industry")
        if ind and ind not in seen:
            industries.append(ind)
            seen.add(ind)
    return QmingpianHitOut(
        person_id=pick.get("person_id"),
        position=pick.get("zhiwu") or None,
        tags=tags,
        industries=industries,
        cards=cards,
    )


class CardUploadOut(BaseModel):
    url: str
    size: Optional[str] = None
    file_name: Optional[str] = None


@router.post("/upload-business-card", response_model=CardUploadOut)
async def upload_business_card(
    file: UploadFile = File(...),
    _: dict = Depends(get_current_ir),
):
    """名片上传：把 IR 选好的图片转发给企名片 /Upload/file，存到企名片侧 OSS。
    返回 URL 由前端写入 form.business_card_url 并随 PUT/POST 持久化到本地 DB。

    生产部署提醒：返回 URL 域名 qimingpianfile.investarget.com 必须加入小程序
    后台「downloadFile 合法域名」白名单，否则 image 标签加载不出。
    """
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片超过 20MB")
    try:
        res = await qmingpian_upload_file(
            file_bytes=content,
            filename=file.filename or "card.jpg",
            mime_type=file.content_type or "image/jpeg",
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"企名片上传失败: {e}")
    url = (res or {}).get("url")
    if not url:
        raise HTTPException(status_code=502, detail=f"企名片未返回 url: {res}")
    return CardUploadOut(
        url=url,
        size=str(res.get("size") or ""),
        file_name=res.get("file_name"),
    )


@router.get("/qmingpian/by-name", response_model=EnrichedQmingpianOut)
async def enrich_from_qmingpian(
    person_name: str = Query(..., min_length=1, description="投资人姓名"),
    expected_agency: Optional[str] = Query(None, description="本地机构名，用于同名消歧（无 person_id 时 fallback）"),
    person_id: Optional[str] = Query(None, description="企名片 person_id（推荐）— 精准定位避免同名"),
    _: dict = Depends(get_current_ir),
):
    """从企名片 exportPersonOpen 拉投资人详情（机构/手机/邮箱/行业/纪要/历史推荐/熟悉人）。

    优先 person_id（精准）；只传 person_name 时按 expected_agency fuzzy 选行。
    选不出对的或调用失败 → 200 + 空字段，避免污染详情。"""
    try:
        data = await qmingpian_export_person(
            person_name=person_name,
            expected_agency=expected_agency or "",
            person_id=person_id or "",
        )
    except Exception:
        return EnrichedQmingpianOut()
    if not data or not isinstance(data, dict):
        return EnrichedQmingpianOut()
    # 仅在没传 person_id 而走 name fuzzy 时再校验 agency；person_id 命中即权威
    if not person_id and expected_agency:
        from agent.orchestrator_tools.direct import _same_agency
        if not _same_agency(data.get("agency") or "", expected_agency):
            return EnrichedQmingpianOut()
    return EnrichedQmingpianOut(
        agency=data.get("agency"),
        phone=data.get("phone"),
        email=data.get("email"),
        industry=data.get("industry"),
        summaries=[QmingpianSummary(**s) for s in data.get("summaries", [])],
        history=[QmingpianHistory(**h) for h in data.get("history", [])],
        familiar_persons=[QmingpianFamiliarPerson(**f) for f in data.get("familiar_persons", [])],
    )


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
        # 调企名片新增（一次性带上扩展字段 + tag，省去后续单独 updatePersonTag）
        try:
            res = await qmingpian_add_person(
                name=body.name,
                agency=body.agency or "",
                phone=_first_or_empty(body.phone),
                wechat=_first_or_empty(body.wechat),
                email=_first_or_empty(body.email),
                position=body.position or "",
                tags=body.qmingpian_tags,
                level=body.level or "",
                gender=body.gender or "",
                office_location=body.office_location or "",
                introduction=body.introduction or "",
                is_dimission=body.is_dimission,
            )
            person_id = res.get("person_id")
            if not person_id:
                raise HTTPException(status_code=502, detail=f"企名片未返回 person_id: {res}")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"企名片新增失败: {e}")

    warnings: list[str] = []
    # 投资人标签：only call updatePersonTag if 已有 person_id（从搜索结果加入路径）
    # 否则上面 addPersonInfo 已经带了 tags
    if (body.qmingpian_person_id and body.qmingpian_tags is not None
            and body.qmingpian_tags):
        try:
            await qmingpian_update_person_tags(
                name=body.name,
                agency=body.agency or "",
                tags=body.qmingpian_tags,
            )
        except Exception as e:
            logger.warning("qmingpian tags sync failed on create for %s: %s", body.name, e)
            warnings.append(f"投资人标签未同步至企名片：{e}")

    # 名片绑定到企名片（让企名片 PC 端能看到这张名片）
    if body.business_card_url and person_id:
        try:
            ir_row = (await db.execute(
                select(IRUser).where(IRUser.id == _["ir_id"])
            )).scalar_one_or_none()
            create_name = ((ir_row.qmingpian_username if ir_row and ir_row.qmingpian_username
                            else (ir_row.name if ir_row else ""))
                           or "Investarget")
            await qmingpian_add_person_card(
                person_id=person_id,
                img_url=body.business_card_url,
                create_name=create_name,
            )
        except Exception as e:
            logger.warning("qmingpian addPersonCard failed on create for %s: %s", body.name, e)
            warnings.append(f"名片未绑定至企名片：{e}")

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
    out = InvestorOut.model_validate(investor)
    out.qmingpian_warnings = warnings
    return out


@router.put("/{investor_id}", response_model=InvestorOut)
async def update_investor(
    investor_id: int,
    body: InvestorUpdate,
    db: AsyncSession = Depends(get_db),
    current_ir: dict = Depends(get_current_ir),
):
    """
    编辑：
    - 同步基本信息（name/agency/position/phone/wechat/email）到企名片；
    - 熟悉度（familiarity）回写到企名片（若 IR 配置了 qmingpian_username）；
    - 其他扩展字段写本地。
    """
    result = await db.execute(select(Investor).where(Investor.id == investor_id))
    investor = result.scalar_one_or_none()
    if not investor:
        raise HTTPException(status_code=404, detail="投资人不存在")

    updates = body.model_dump(exclude_unset=True)
    # qmingpian_tags 不入本地表，单独处理
    qmingpian_tags_update = updates.pop("qmingpian_tags", None)
    warnings: list[str] = []

    # 1) 同步基本信息到企名片：仅对真正变了的字段调 editPersonInfo；
    # 失败降级为 warning，不阻塞本地保存（与熟悉度/标签回写一致）
    qmingpian_changes = {}
    for k in _QMINGPIAN_FIELDS:
        if k not in updates:
            continue
        new_v = updates[k]
        cur_v = getattr(investor, k, None)
        if new_v != cur_v:
            qmingpian_changes[k] = new_v
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
            logger.warning(
                "qmingpian edit_person failed for investor %s (changes=%s): %s",
                investor_id, qmingpian_changes, e,
            )
            warnings.append(f"基本信息未同步至企名片：{e}")

    # 2) 熟悉度回写企名片（需要 IR 配置了 qmingpian_username）
    # 首次设置走 add，已有历史值走 update（语义更清晰，避免 add 重复造副作用）
    new_familiarity = updates.get("familiarity")
    if new_familiarity and new_familiarity != investor.familiarity:
        ir_result = await db.execute(
            select(IRUser).where(IRUser.id == current_ir["ir_id"])
        )
        ir_user = ir_result.scalar_one_or_none()
        if ir_user and ir_user.qmingpian_username:
            try:
                name_for_qm = updates.get("name") or investor.name
                agency_for_qm = updates.get("agency") or investor.agency or ""
                sync_fn = (qmingpian_update_familiar_person
                           if investor.familiarity
                           else qmingpian_add_familiar_person)
                await sync_fn(
                    name=name_for_qm,
                    agency=agency_for_qm,
                    user_name=ir_user.qmingpian_username,
                    level=new_familiarity,
                )
            except Exception as e:
                logger.warning(
                    "qmingpian familiarity sync failed for investor %s: %s",
                    investor_id, e,
                )
                warnings.append(f"熟悉度未同步至企名片：{e}")

    # 3) 投资人标签回写企名片（updatePersonTag, 本地不存）
    if qmingpian_tags_update is not None:
        try:
            name_for_qm = updates.get("name") or investor.name
            agency_for_qm = updates.get("agency") or investor.agency or ""
            await qmingpian_update_person_tags(
                name=name_for_qm,
                agency=agency_for_qm,
                tags=qmingpian_tags_update,
            )
        except Exception as e:
            logger.warning(
                "qmingpian tag sync failed for investor %s: %s",
                investor_id, e,
            )
            warnings.append(f"投资人标签未同步至企名片：{e}")

    # 3.5) 名片绑卡到企名片（business_card_url 变化时；首次设置或更换都触发）
    new_card_url = updates.get("business_card_url")
    if (new_card_url and new_card_url != investor.business_card_url
            and investor.qmingpian_person_id):
        try:
            ir_row = (await db.execute(
                select(IRUser).where(IRUser.id == current_ir["ir_id"])
            )).scalar_one_or_none()
            create_name = ((ir_row.qmingpian_username if ir_row and ir_row.qmingpian_username
                            else (ir_row.name if ir_row else ""))
                           or "Investarget")
            await qmingpian_add_person_card(
                person_id=investor.qmingpian_person_id,
                img_url=new_card_url,
                create_name=create_name,
            )
        except Exception as e:
            logger.warning(
                "qmingpian addPersonCard failed for investor %s: %s",
                investor_id, e,
            )
            warnings.append(f"名片未绑定至企名片：{e}")

    # 4) 写本地（全部字段，包括 qmingpian 同步过的，保持镜像）
    for field, value in updates.items():
        setattr(investor, field, value)
    await db.commit()
    await db.refresh(investor)
    out = InvestorOut.model_validate(investor)
    out.qmingpian_warnings = warnings
    return out


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
