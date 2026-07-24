# 数据库备份与恢复

## 文件说明

| 脚本 | 说明 |
|------|------|
| `db_backup.sh` | 手动备份，支持指定目录/文件路径 |
| `db_backup_auto.sh` | 定时自动备份（本地，保留最新 7 份） |
| `db_backup_cloud.sh` | 备份 + 双云盘上传（Google Drive + Mega），同时备份 .env |
| `db_restore.sh` | 从本地备份文件恢复数据库 |
| `db_restore_cloud.sh` | 从云盘下载备份并恢复数据库 |
| `db_cloud_test.sh` | 云盘连通性测试（小型备份 → 上传 → 验证 → 清理） |

## 快速开始

### 本地备份

```bash
# 手动备份（保存到 ./backups/）
bash scripts/db_backup/db_backup.sh

# 设定时任务（crontab -e）
17 3 * * * cd /root/audiobook && bash scripts/db_backup/db_backup_auto.sh >> backups/cron.log 2>&1
```

### 云盘备份（推荐）

#### 1. 安装 rclone 并配置远程

```bash
curl https://rclone.org/install.sh | bash
rclone config
# 新建 gdrive → Google Drive（浏览器授权）
# 新建 mega  → Mega（邮箱密码）
```

确认配置：

```bash
rclone listremotes
# 应显示:
# gdrive:
# mega:
```

#### 2. 测试连通性

```bash
bash scripts/db_backup/db_cloud_test.sh
```

全 ✅ 后继续。

#### 3. 手动跑一次正式备份

```bash
bash scripts/db_backup/db_backup_cloud.sh
```

#### 4. 设定时任务

```bash
crontab -e
```

```
17 3 * * * cd /root/audiobook && bash scripts/db_backup/db_backup_cloud.sh >> backups/cron.log 2>&1
```

### 恢复

```bash
# 从本地备份恢复
bash scripts/db_backup/db_restore.sh backups/audiobook_backup_20260724_031700.sql.gz

# 从云盘恢复（交互式选择备份文件）
bash scripts/db_backup/db_restore_cloud.sh

# 从 Mega 恢复 + 同时恢复 .env
bash scripts/db_backup/db_restore_cloud.sh --source mega --with-env

# 仅列出云端备份文件
bash scripts/db_backup/db_restore_cloud.sh --list

# 指定文件名恢复
bash scripts/db_backup/db_restore_cloud.sh --file audiobook_backup_20260724_031700.sql.gz
```

恢复后重启 Web 服务：

```bash
docker-compose restart web
```

## 备份内容

| 内容 | 本地备份 | 云盘备份 |
|------|---------|---------|
| 数据库全量（表结构 + 数据） | ✅ | ✅ |
| `.env` 配置文件（含 SECRET_KEY） | ❌ | ✅ |

> **关键**：`.env` 中的 `SECRET_KEY` 用于加密 `channels.oauth_client_secret` 等敏感数据。灾难恢复时必须使用相同的 `SECRET_KEY`，否则加密数据无法解密。云盘备份自动包含 `.env`。

## 保留策略

| 备份方式 | 本地保留 | 云端保留 |
|---------|---------|---------|
| `db_backup_auto.sh` | 7 份 | — |
| `db_backup_cloud.sh` | 7 份 | 7 份 |

## 脚本参数参考

### db_backup.sh

| 参数 | 说明 |
|------|------|
| 无参数 | 备份到 `./backups/` |
| `<目录>` | 备份到指定目录 |
| `<文件路径>` | 备份到指定文件（需 `.sql.gz` 结尾） |

### db_restore_cloud.sh

| 参数 | 说明 |
|------|------|
| `--source gdrive\|mega` | 从指定云盘下载（默认 gdrive） |
| `--list` | 仅列出云端备份，不恢复 |
| `--file <文件名>` | 指定备份文件名 |
| `--with-env` | 同时从云端恢复 .env 配置 |

## 注意事项

- crontab 中需设置 `HOME=/root`，否则 rclone 找不到配置文件
- Google Drive 免费空间 15GB，Mega 免费空间 20GB，一般足够
- 云盘同步日志位于 `backups/cloud_sync.log`
- 恢复操作会先停止 Web 和中继服务，恢复后自动重启
