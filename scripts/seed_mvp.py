"""
MVP 联调种子数据脚本。

运行：
    cd /Users/summer/fa-agent/backend
    DATABASE_URL=mysql+aiomysql://root:password@host:3306/fa_agent \
        python ../scripts/seed_mvp.py

或者使用现有 .env：
    cd /Users/summer/fa-agent/backend
    python ../scripts/seed_mvp.py

数据：
- 5 个测试 IR 用户（带手机号 13800000001-005，phone 唯一）
- 20 个投资人（5 个生日在 7 天内，触发 milestone）
- 30 个 outreach_records（覆盖 4 种 type、3 种 status）
- 10 条 interaction_logs（混合 type）

幂等：每次运行前检查 phone='13800000001' 是否存在，存在则跳过创建用户（避免唯一冲突），其他数据每次清空重建。

⚠️  警告：默认 .env 指向生产 DB（39.107.14.53）。
    请用 DATABASE_URL 环境变量覆盖，避免污染生产数据：
    DATABASE_URL=mysql+aiomysql://root:pass@localhost:3306/fa_agent_test python seed_mvp.py
"""

from __future__ import annotations
import asyncio
import sys
import os
import random
from datetime import datetime, date, timedelta

# 添加 backend 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from database import AsyncSessionLocal
from models.ir_users import IRUser
from models.investors import Investor
from models.interaction_logs import InteractionLog
from models.outreach_records import OutreachRecord


SEED_PHONE_PREFIX = "138000000"   # 测试号段，便于识别
SEED_NAMES = ["张伟", "李明", "王芳", "刘强", "赵丽"]
AGENCIES = ["红杉资本", "高榕资本", "IDG资本", "经纬创投", "真格基金", "源码资本", "GGV纪源资本", "DCM"]
INDUSTRIES = ["消费", "TMT", "医疗", "AI", "SaaS", "硬件", "教育", "金融"]
STAGES = ["天使", "A轮", "B轮", "C轮"]


async def seed_users(db) -> list[IRUser]:
    """5 个测试 IR。如果已存在跳过创建。"""
    users = []
    for i, name in enumerate(SEED_NAMES, 1):
        phone = f"{SEED_PHONE_PREFIX}{i:02d}"
        existing = (await db.execute(
            select(IRUser).where(IRUser.phone == phone)
        )).scalar_one_or_none()
        if existing:
            users.append(existing)
            continue
        user = IRUser(
            name=name,
            phone=phone,
            role="admin" if i == 1 else "ir",
            is_active=True,
        )
        db.add(user)
        try:
            await db.commit()
            await db.refresh(user)
            users.append(user)
        except IntegrityError:
            await db.rollback()
            existing = (await db.execute(
                select(IRUser).where(IRUser.phone == phone)
            )).scalar_one_or_none()
            if existing:
                users.append(existing)
    return users


async def seed_investors(db) -> list[Investor]:
    """20 个投资人。先清空所有 quota_range='SEED-MVP' 的测试投资人再创建。"""
    await db.execute(delete(Investor).where(Investor.quota_range == "SEED-MVP"))
    await db.commit()

    today = date.today()
    names = ["张三", "李四", "王五", "赵六", "钱七", "孙八", "周九", "吴十",
             "郑伟", "冯敏", "陈红", "卫强", "蒋丽", "沈昊", "韩雷", "杨光",
             "朱琴", "秦悦", "尤洁", "许涛"]

    investors = []
    for i, name in enumerate(names):
        # 前 5 个生日在 7 天内（触发 milestone）
        if i < 5:
            birthday_md = today + timedelta(days=random.randint(0, 6))
            birthday = birthday_md.replace(year=today.year - random.randint(30, 50))
        else:
            # 其他随机一年中（明确排开 7 天窗口）
            birthday = date(today.year - random.randint(30, 50),
                            random.randint(1, 12), random.randint(1, 28))

        # 前 10 个 last_interaction_at > 14 天前（触发 followup）
        if i < 10:
            last_inter = datetime.now() - timedelta(days=random.randint(15, 45))
        else:
            last_inter = datetime.now() - timedelta(days=random.randint(1, 10))

        inv = Investor(
            name=name,
            agency=random.choice(AGENCIES),
            position=random.choice(["合伙人", "投资经理", "高级投资经理", "总监"]),
            industry_tags=random.sample(INDUSTRIES, k=random.randint(1, 3)),
            stage_pref=random.sample(STAGES, k=random.randint(1, 2)),
            quota_range="SEED-MVP",   # 标记，便于清理
            relationship_score=random.randint(1, 5),
            profile_notes=(
                f"[{today - timedelta(days=10)}] 关注{random.choice(INDUSTRIES)}赛道\n"
                f"[{today - timedelta(days=5)}] 看重团队执行力"
            ),
            birthday=birthday,
            last_interaction_at=last_inter,
            is_active=True,
        )
        db.add(inv)
        investors.append(inv)

    await db.commit()
    for inv in investors:
        await db.refresh(inv)
    return investors


async def seed_outreach(db, users: list[IRUser], investors: list[Investor]):
    """30 个 outreach_records。先清当前测试 IR 的旧记录。"""
    user_ids = [u.id for u in users]
    await db.execute(delete(OutreachRecord).where(OutreachRecord.ir_id.in_(user_ids)))
    await db.commit()

    types = ["meeting_minutes", "industry_report", "daily_push", "milestone_message"]
    statuses = ["draft", "approved", "sent"]
    sample_texts = [
        "本次会议讨论了投资人对消费赛道的关注点，建议下次重点介绍 GMV 数据及复购率。",
        "建议在 Q4 重点跟进医疗器械方向，该投资人近期对创新医疗器械标的兴趣明显提升。",
        "祝您生日快乐！期待新一年继续深化合作，共同见证更多优质项目的成长。",
        "本周行业要闻：某头部消费品牌完成 B 轮融资，估值达 15 亿，市场关注度显著提升。",
    ]

    for i in range(30):
        rec = OutreachRecord(
            investor_id=random.choice(investors).id,
            ir_id=random.choice(user_ids),
            type=types[i % len(types)],
            channel="wechat",
            content=f"[Seed #{i+1}] " + random.choice(sample_texts),
            status=statuses[i % 3],
        )
        db.add(rec)

    await db.commit()


async def seed_interactions(db, users: list[IRUser], investors: list[Investor]):
    """10 条手动 interaction 记录。"""
    user_ids = [u.id for u in users]
    await db.execute(
        delete(InteractionLog)
        .where(InteractionLog.ir_id.in_(user_ids))
        .where(InteractionLog.agent_generated == False)   # noqa: E712
    )
    await db.commit()

    types = ["meeting", "wechat", "call", "email", "other"]
    for i in range(10):
        log = InteractionLog(
            investor_id=random.choice(investors).id,
            ir_id=random.choice(user_ids),
            type=random.choice(types),
            summary=f"[Seed #{i+1}] 沟通了 {random.choice(INDUSTRIES)} 方向的项目进展及下一步计划。",
            agent_generated=False,
        )
        db.add(log)
    await db.commit()


async def main():
    print("开始注入 MVP 种子数据...")
    print("⚠️  请确认当前 DATABASE_URL 指向测试环境，避免污染生产数据！\n")

    async with AsyncSessionLocal() as db:
        print("--- IR 用户 ---")
        users = await seed_users(db)
        for u in users:
            print(f"  ✓ {u.name} (role={u.role}, phone={u.phone}, id={u.id})")

        print("\n--- 投资人 ---")
        investors = await seed_investors(db)
        print(f"  ✓ 创建 {len(investors)} 个投资人")
        today = date.today()
        upcoming_bday = [
            inv for inv in investors
            if inv.birthday and 0 <= (inv.birthday.replace(year=today.year) - today).days < 7
        ]
        print(f"    其中 {len(upcoming_bday)} 个生日在 7 天内（触发 milestone）")

        print("\n--- Outreach 草稿 ---")
        await seed_outreach(db, users, investors)
        print("  ✓ 创建 30 条 outreach_records（覆盖 4 种 type、3 种 status）")

        print("\n--- Interaction 手动记录 ---")
        await seed_interactions(db, users, investors)
        print("  ✓ 创建 10 条 interaction_logs（混合 type）")

        print("\n完成。可以用以下账号联调：")
        for u in users[:3]:
            print(f"  {u.name} (role={u.role}, phone={u.phone})")
        print(
            "\n注意：openid 字段为空，IR 首次登录小程序时会触发手机号绑定流程。\n"
            "清理：DELETE FROM investors WHERE quota_range='SEED-MVP'；\n"
            "      DELETE FROM outreach_records/interaction_logs WHERE ir_id IN (seed IR ids)。"
        )


if __name__ == "__main__":
    asyncio.run(main())
