# 部署脚本

## 文件说明

| 脚本 | 说明 |
|------|------|
| `git-server-deploy.sh` | 服务器端一键部署脚本（git pull → 智能构建 → 重启 → 健康检查） |

## 用法

在服务器上执行：

```bash
cd /root/audiobook
bash scripts/deploy/git-server-deploy.sh
```

## 执行流程

```
[1/4] git pull                    拉取最新代码（脚本自身更新会自动重新执行）
[2/4] 检查 .env                    确保配置文件存在
[2.5/4] 检查 DeepFilter            预下载降噪二进制到 data/deepfilter/
[2.6/4] 检查 BGM 音乐库            预下载音乐包到 data/music/
[2.7/4] 检查 5432 端口占用          关闭系统自带 PostgreSQL / 残留容器
[3/4] Docker 构建                   智能检测变更，仅在必要时重建镜像
[4/4] 健康检查                      等待 Web 服务和中继调度器就绪
```

## 智能构建

脚本通过 hash 检测以下文件是否变更，仅在必要时重建 Docker 镜像：

| 检查项 | 缓存文件 |
|--------|---------|
| `requirements.txt` | `.cache_req_hash` |
| `docker/Dockerfile.web` + `hf_workers/vps_relay/Dockerfile` | `.cache_docker_hash` |
| `backend/` + `pipeline/` + `docker/` + `hf_workers/vps_relay/` 源码 | `.cache_src_hash` |

无变更时跳过构建，仅重启容器（通常 10-30 秒）。

## 首次部署

首次部署约需 5-10 分钟（下载镜像 + 构建依赖 + 下载 DeepFilter/BGM）。

前置条件：
- 已 `git clone` 项目仓库到服务器
- 已安装 Docker + Docker Compose
- 已创建 `.env` 配置文件（`cp .env.example .env && nano .env`）

详细部署步骤见项目根目录 [部署指南.md](../../部署指南.md)。

## 日常更新

开发机推送代码后，在服务器上执行：

```bash
cd /root/audiobook
bash scripts/deploy/git-server-deploy.sh
```

## 注意事项

- 脚本支持自更新：如果 `git pull` 更新了脚本自身，会自动用新版本重新执行
- 5432 端口检查会自动关闭系统自带的 PostgreSQL 服务和残留的 Docker 容器
- 部署完成后会显示服务状态和访问地址
