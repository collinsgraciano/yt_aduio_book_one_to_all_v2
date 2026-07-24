# 数据迁移

从旧项目或其他 VPS 迁移数据到本项目。

## 文件说明

| 脚本 | 说明 |
|------|------|
| `migrate_data.sh` | 从旧 PostgreSQL 迁移核心表数据（books、youtube_credentials 等 6 张表） |
| `books_transfer.sh` | books 表直传到目标 VPS（源容器 pg_dump → 管道 → 目标 psql） |
| `books表迁移指南.md` | books_transfer.sh 的详细使用文档 |

## 场景对比

| 场景 | 脚本 | 迁移范围 |
|------|------|---------|
| 从旧项目迁移核心数据 | `migrate_data.sh` | 6 张核心表（books、youtube_credentials 等） |
| 同项目间同步 books 表 | `books_transfer.sh` | 仅 books 表 |

## migrate_data.sh — 从旧 PostgreSQL 迁移

### 迁移的表

| 表名 | 说明 |
|------|------|
| `books` | 书籍库（含 book_data JSON） |
| `book_processing_states` | 断点续跑状态 |
| `youtube_credentials` | YouTube OAuth Token |
| `modelscopes_tokens` | AI 生图 Token |
| `channel_runtime_settings` | 频道运行时配置 |
| `task_queue` | 原始任务队列 |

> **不迁移的表**：`global_settings`、`channels`、`channel_configs`、`audiobook_chapters` — 这些需在 Web 界面中重新配置。

### 用法

```bash
# 设置旧数据库连接信息
export OLD_PG_HOST=127.0.0.1        # 旧数据库地址
export OLD_PG_PORT=5432              # 旧数据库端口
export OLD_PG_USER=postgres          # 旧数据库用户
export OLD_PG_DB=audiobook           # 旧数据库名
export OLD_PG_PASSWORD=your_old_password

# 执行迁移
bash scripts/data_migration/migrate_data.sh
```

远程旧数据库：

```bash
export OLD_PG_HOST=85.121.48.55
export OLD_PG_PASSWORD=your_remote_password
bash scripts/data_migration/migrate_data.sh
```

### 执行流程

1. 测试旧数据库连接
2. 逐表导出（`pg_dump --data-only --column-inserts`）
3. 验证新数据库表结构
4. 清空目标表后导入（禁用外键约束）
5. 逐表对比行数验证

### 迁移后配置

以下内容需在 Web 界面中重新配置：

1. **全局设置**：`MODELSCOPE_TOKEN`、`TG_BOT_TOKEN` 等
2. **频道注册**：添加频道 + 上传 OAuth `client_secret.json` + 授权
3. **频道运行配置**：95+ 参数

## books_transfer.sh — books 表直传

详细使用文档见 [books表迁移指南.md](books表迁移指南.md)。

### 快速用法

```bash
# 基本用法（交互确认）
bash scripts/data_migration/books_transfer.sh --dsn "postgresql://audiobook_app:PASSWORD@TARGET_IP:5432/audiobook"

# 后台运行（大数据量推荐）
bash scripts/data_migration/books_transfer.sh --dsn "..." --force --bg

# 查看后台进度
tail -f backups/transfer.log
```

### 参数

| 参数 | 说明 |
|------|------|
| `--dsn <连接串>` | 目标 VPS 的 PostgreSQL 连接字符串（必须） |
| `--container <名称>` | 源 PostgreSQL 容器名（默认 `audiobook_postgres`） |
| `--force` | 跳过确认提示 |
| `--bg` | 后台运行，日志输出到 `backups/transfer.log` |

### 安全机制

- 传输前自动备份目标 VPS 当前的 books 表
- 传输后自动验证行数和数据统计
- 支持 `--bg` 后台运行，适合大数据量
