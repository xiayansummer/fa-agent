# scripts/

## seed_mvp.py — 联调种子数据

注入 5 个测试 IR + 20 个投资人（5 个生日在 7 天内）+ 30 个 outreach 草稿 + 10 条互动记录。

### 运行

```bash
cd /Users/summer/fa-agent/backend
python ../scripts/seed_mvp.py
```

### ⚠️  避免污染生产 DB

默认 `.env` 指向生产 DB（39.107.14.53）。请用 `DATABASE_URL` 覆盖到测试环境再跑：

```bash
cd /Users/summer/fa-agent/backend
DATABASE_URL=mysql+aiomysql://root:password@localhost:3306/fa_agent_test \
    python ../scripts/seed_mvp.py
```

### 幂等性

- **IR 用户**：按 `phone` 唯一约束去重，重复运行不会报错。
- **投资人**：每次清空 `quota_range='SEED-MVP'` 的记录再重建。
- **outreach_records / interaction_logs**：每次清空测试 IR id 范围内的记录再重建。

### 清理

```sql
-- 清投资人及其关联记录
DELETE FROM investors WHERE quota_range = 'SEED-MVP';

-- 清 outreach / interaction（替换 id 列表为实际值）
DELETE FROM outreach_records WHERE ir_id IN (1, 2, 3, 4, 5);
DELETE FROM interaction_logs WHERE ir_id IN (1, 2, 3, 4, 5) AND agent_generated = 0;
```
