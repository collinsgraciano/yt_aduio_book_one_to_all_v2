# 有声书 YouTube 频道统一管理系统 (v2 重构版)

多频道管理、视频自动上传、配置可视化编辑、一键 Docker 部署。

## 快速部署

```bash
# 1. 克隆到服务器
git clone https://github.com/YOUR_USER/YOUR_REPO.git /root/audiobook
cd /root/audiobook

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，设置 POSTGRES_PASSWORD, SECRET_KEY, BASE_URL

# 3. 一键部署
bash scripts/git-server-deploy.sh
```

浏览器打开 `http://服务器IP:59386`，默认密码 `inriynisse`。

## 日常更新

```bash
# 开发机推送
git add -A && git commit -m "更新" && git push

# 服务器拉取部署
cd /root/audiobook && bash scripts/git-server-deploy.sh
```

## 开发模式

```bash
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up
```

## 服务端口

| 服务 | 端口 | 说明 |
|------|------|------|
| web | 59386 | FastAPI Web 服务 |
| postgres | 5432 | PostgreSQL 16 |

## 技术栈

| 层级 | 技术 |
|------|------|
| Web 框架 | FastAPI + Uvicorn |
| 数据库 | PostgreSQL 16（Docker 内置） |
| 前端 | Jinja2 服务端渲染（无前端构建步骤） |
| 任务执行 | Python `threading.Thread`（无需 Redis/Celery） |
| 容器 | Docker Compose（web + postgres 两服务） |

## 文档

- [部署指南.md](部署指南.md) — 完整部署教程（首次部署、日常更新、OAuth 配置、常见问题）
- [数据库备份恢复迁移指南.md](数据库备份恢复迁移指南.md) — 备份、恢复、从旧项目迁移
- [CHANGES.md](CHANGES.md) — v2 重构变更记录

## 目录结构

```
v2/
├── docker-compose.yml          # 主编排配置（web + postgres）
├── docker-compose.dev.yml      # 开发环境覆盖（热重载）
├── .env.example                # 环境变量模板
├── requirements.txt            # Python 依赖
├── 部署指南.md
├── 数据库备份恢复迁移指南.md
├── CHANGES.md
├── docker/
│   ├── Dockerfile.web          # Web 服务镜像
│   ├── entrypoint.sh           # 启动脚本（DeepFilter + BGM 初始化）
│   └── init-db.sql             # 数据库初始化（15 张表）
├── scripts/
│   ├── git-server-deploy.sh    # 服务器端部署脚本
│   ├── db_backup.sh            # 数据库备份
│   ├── db_restore.sh           # 数据库恢复
│   └── migrate_data.sh         # 从旧项目迁移数据
├── backend/                    # Web 管理系统（FastAPI）
├── pipeline/                   # 核心处理流水线
└── hf_workers/                 # HF 外包架构（独立部署）
```
