# books 表迁移指南

## 1. 概述

将源 VPS 的 `books` 表数据直接传输到目标 VPS，**替换**目标 VPS 的 books 表。

### 原理

```
源 VPS                                     目标 VPS
┌─────────────────────┐                   ┌─────────────────────┐
│ audiobook_postgres  │                   │ audiobook_postgres  │
│                     │                   │                     │
│  pg_dump books ─────┼── 直连 5432 ────►│  psql               │
│  (源容器导出)        │   管道传输         │  (TRUNCATE+INSERT)  │
└─────────────────────┘                   └─────────────────────┘
```

源容器的 `pg_dump` 导出 books 表数据，通过管道直接喂给源容器的 `psql` 连接目标 VPS 的 PostgreSQL，一步完成 TRUNCATE + 导入。

### 迁移内容

| 内容 | 说明 |
|------|------|
| `books` 表数据 | 书籍库（含 `book_data` JSON、章节状态、分类标签等） |

> **不迁移的内容**：`channels`、`channel_configs`、`global_settings`、`youtube_credentials`、`audiobook_chapters` 等其他表不受影响。

### 安全机制

- 传输前自动备份目标 VPS 当前的 books 表
- 传输后自动验证行数和数据统计

---

## 2. 前置条件

### 目标 VPS：放行 5432 端口

```bash
# 在目标 VPS 上执行
ufw allow 5432/tcp
```

### 获取目标 VPS 的 PostgreSQL 连接信息

```bash
# 在目标 VPS 上执行
grep POSTGRES_PASSWORD /root/audiobook/.env
```

连接串格式：`postgresql://audiobook_app:<密码>@<目标IP>:5432/audiobook`

---

## 3. 操作步骤

```bash
# ═══ 在源 VPS 上执行 ═══
cd /root/audiobook

# 基本用法（交互确认）
bash scripts/books_transfer.sh --dsn "postgresql://audiobook_app:PASSWORD@TARGET_IP:5432/audiobook"

# 跳过确认
bash scripts/books_transfer.sh --dsn "postgresql://audiobook_app:PASSWORD@TARGET_IP:5432/audiobook" --force
```

**实际示例：**

```bash
bash scripts/books_transfer.sh --dsn "postgresql://audiobook_app:inriynisse1991@85.121.48.55:5432/audiobook"
```

脚本执行流程：

```
[1/5] 测试目标 PostgreSQL 连接     ← 确认可连通
[2/5] 获取数据统计                 ← 源行数 vs 目标行数
[3/5] 备份目标 books 表           ← 先备份到源 VPS 本机
[4/5] 传输数据                    ← pg_dump → 管道 → psql 直连目标
[5/5] 验证                        ← 对比行数 + 数据统计
```

输出示例：

```
═══════════════════════════════════════════════════
  books 表直传
  源:   本机 audiobook_postgres
  目标: postgresql://audiobook_app:inriynisse1991@85.121.48.55:5432/audiobook
═══════════════════════════════════════════════════
[1/5] 测试目标 PostgreSQL 连接...
[OK]  目标 PostgreSQL 连接成功
[2/5] 获取数据统计...
[INFO] 源 VPS   books 行数: 1234
[INFO] 目标 VPS books 行数: 567
[3/5] 备份目标 VPS 的 books 表...
[OK]  已备份到: backups/target_books_backup_20260721_120000.sql.gz (1.2M)
[4/5] 传输数据（TRUNCATE + 导入）...
[OK]  数据传输完成
[5/5] 验证...
[OK]  行数匹配: 1234 行
═══════════════════════════════════════════════════
  传输完成
  源 VPS   books: 1234 行
  目标 VPS books: 1234 行（替换前: 567 行）
═══════════════════════════════════════════════════
```

### 完成后

```bash
# 在目标 VPS 上重启 Web 服务（使界面加载新数据）
docker-compose restart web
```

---

## 4. 验证

### 命令行验证（在目标 VPS 上）

```bash
# 查看行数
docker exec audiobook_postgres psql -U audiobook_app -d audiobook -c "SELECT COUNT(*) FROM books;"

# 查看状态分布
docker exec audiobook_postgres psql -U audiobook_app -d audiobook -c "
    SELECT
        COUNT(*) AS total,
        COUNT(*) FILTER (WHERE book_status = 'success') AS success,
        COUNT(*) FILTER (WHERE book_status = 'pending') AS pending,
        COUNT(DISTINCT category) AS categories
    FROM books;"

# 查看前 10 条
docker exec audiobook_postgres psql -U audiobook_app -d audiobook -c "
    SELECT book_id, book_name, category, book_status FROM books ORDER BY updated_at DESC LIMIT 10;"
```

### Web 界面验证

1. 浏览器打开 `http://目标VPS_IP:59386`
2. 进入书籍管理页面
3. 确认书籍列表、分类、状态正确

---

## 5. 回滚

直传脚本会将目标 VPS 的 books 表备份到**源 VPS** 本机：

```bash
# 1. 将备份传到目标 VPS
scp backups/target_books_backup_20260721_120000.sql.gz root@TARGET_VPS_IP:/root/audiobook/

# 2. 在目标 VPS 上恢复
ssh root@TARGET_VPS_IP
cd /root/audiobook

# 解压并导入
gunzip -c backups/target_books_backup_20260721_120000.sql.gz | \
    docker exec -i audiobook_postgres psql -U audiobook_app -d audiobook

# 重启服务
docker-compose restart web
```

---

## 6. 注意事项

### 6.1 books 表无外键依赖

`books` 表没有被其他表通过外键引用，`TRUNCATE` 不影响其他表。以下表含 `book_id` 但均无 FK 约束：

| 表名 | 影响 |
|------|------|
| `audiobook_chapters` | ❌ 无影响 |
| `book_processing_states` | ❌ 无影响 |
| `task_queue` | ❌ 无影响 |
| `hf_jobs` | ❌ 无影响 |

### 6.2 关联数据清理（可选）

迁移后目标 VPS 可能存在 `book_id` 不在 books 表中的孤立记录，按需清理：

```bash
docker exec audiobook_postgres psql -U audiobook_app -d audiobook -c "
    DELETE FROM audiobook_chapters WHERE book_id NOT IN (SELECT book_id FROM books);
    DELETE FROM book_processing_states WHERE book_id NOT IN (SELECT book_id FROM books);
    DELETE FROM task_queue WHERE book_id NOT IN (SELECT book_id FROM books);
"
```

### 6.3 迁移不影响的内容

- ✅ 频道配置（`channels`、`channel_configs`）
- ✅ YouTube OAuth 凭证（`youtube_credentials`）
- ✅ 全局设置（`global_settings`）
- ✅ HF 中继配置
- ✅ 任务记录（`run_tasks`、`run_task_logs`）
- ✅ `.env` 配置文件

### 6.4 安全提示

连接串中包含数据库密码，注意：

- 不要将包含密码的连接串提交到 Git
- 传输完成后可关闭目标 VPS 的 5432 端口：`ufw deny 5432/tcp`

---

## 7. 脚本参数参考

### books_transfer.sh

| 参数 | 说明 |
|------|------|
| `--dsn <连接串>` | 目标 VPS 的 PostgreSQL 连接字符串（必须） |
| `--force` | 跳过确认提示 |

---

## 8. 完整操作示例

```bash
# ═══ 目标 VPS：放行端口（仅需一次） ═══
ufw allow 5432/tcp

# ═══ 源 VPS：一条命令完成 ═══
cd /root/audiobook
bash scripts/books_transfer.sh --dsn "postgresql://audiobook_app:inriynisse1991@85.121.48.55:5432/audiobook"

# ═══ 目标 VPS：重启服务 ═══
docker-compose restart web
```
