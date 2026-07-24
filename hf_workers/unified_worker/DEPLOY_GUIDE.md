# HF Space 部署指南

> Worker 的全部代码（pipeline + app.py + runner.py + shared.py + requirements.txt）
> 在构建时自动从 GitHub 拉取，HF Space 仓库中**只需一个 Dockerfile**。

---

## 前置条件

- 已安装 `git`（≥ 2.0）
- 已安装 `git-lfs`（HF Space 要求，用于大文件追踪）
- 拥有 Hugging Face 账号，且已创建好 Space
- 知道自己的 HF 用户名和 Space 名称
- **在 Space Settings → Secrets 中配置 `HF_TOKEN`**（用于一键重新部署功能，需有该 Space 的 write 权限）

---

## 首次部署（仅一次）

### 第 1 步：克隆 HF Space 仓库

```bash
git clone https://huggingface.co/spaces/你的用户名/audiobook-worker-1
cd audiobook-worker-1
```

> 如果提示输入密码，使用 HF Access Token（不是账号密码）。
> Token 获取：[huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) → New token → Role 选 Write。

### 第 2 步：复制 Dockerfile

从项目目录复制 Dockerfile 到 HF Space 仓库根目录：

```bash
# Windows (PowerShell)
$PROJECT = "H:\2026_main_project\yt_aduio_book_one_to_all_v2"
Copy-Item "$PROJECT\hf_workers\unified_worker\Dockerfile" "Dockerfile"
```

```bash
# macOS / Linux
PROJECT="/path/to/yt_aduio_book_one_to_all_v2"
cp "$PROJECT/hf_workers/unified_worker/Dockerfile" Dockerfile
```

### 第 3 步：配置 Secrets

在 HF Space 页面 → **Settings** → **Secrets** 中添加：

| 名称 | 值 | 说明 |
|------|------|------|
| `HF_TOKEN` | `hf_xxxxxxxx` | HF Access Token（write 权限），用于一键重新部署 |
| `POSTGRES_DSN` | `postgresql://...` | 数据库连接串 |
| `VPS_RELAY_URL` | `http://your-vps:38080` | VPS 中继地址 |
| `MUSIC_ZIP_URL` | *(可选)* | BGM 音乐包 URL |

### 第 4 步：提交并推送

```bash
git add -A
git commit -m "首次部署: Dockerfile"
git push origin main
```

> 推送后 HF Space 会自动开始构建。构建时间约 5-10 分钟（下载 GitHub 代码 + DeepFilter）。

---

## 日常更新（推荐方式）

代码更新后只需两步：

### 第 1 步：推送到 GitHub

```bash
cd /path/to/yt_aduio_book_one_to_all_v2
git add -A
git commit -m "更新代码"
git push origin main
```

### 第 2 步：点击重新部署

打开 Worker 状态面板（`https://你的用户名-audiobook-worker-1.hf.space`），
点击 **🔄 重新部署** 按钮，确认后 Space 会从 Dockerfile 完整重建，
自动从 GitHub 拉取最新代码。

> 也可以调用 API：`POST /redeploy`

---

## 验证部署

### 1. 检查构建状态

推送后访问 HF Space 页面，查看右上角构建状态：
- 🟡 **Building** — 正在构建，等待完成
- 🟢 **Running** — 构建成功，已启动
- 🔴 **Error** — 构建失败，点击 Logs 查看错误

### 2. 健康检查

```bash
curl https://你的用户名-audiobook-worker-1.hf.space/health
```

预期返回：
```json
{
  "ok": true,
  "worker_id": "hf_xxxx",
  "free_slots": 1,
  "test_free_slots": 1
}
```

### 3. BGM 状态

```bash
curl https://你的用户名-audiobook-worker-1.hf.space/bgm-status
```

---

## 常见问题

### Q: 构建失败 `COPY failed: file not found`

Dockerfile 中已无 `COPY` 指令（全部从 GitHub 拉取），请确认 HF Space 仓库根目录有 Dockerfile 文件。

### Q: 构建失败 `wget: unable to resolve host address`

HF Space 构建环境无法访问 GitHub。检查网络或稍后重试。

### Q: 重新部署按钮报错 `HF_TOKEN 未配置`

在 HF Space 页面 → **Settings** → **Secrets** 中添加 `HF_TOKEN`（HF Access Token，write 权限）。

### Q: 重新部署按钮报错 `无法获取 Space 信息`

HF Space 自动注入 `SPACE_AUTHOR_NAME` 和 `SPACE_REPO_NAME` 环境变量，通常无需手动配置。如果报错，确认 Space 是通过标准方式创建的。

### Q: 推送后没有触发重新构建

重新部署是通过 `/redeploy` API 触发的，不是 `git push`。push 代码到 GitHub 后，需要在面板点击"重新部署"按钮。

### Q: 构建成功但 /health 返回 502

HF Space 冷启动需要 30-60 秒。等待片刻后重试：
```bash
sleep 30 && curl https://你的用户名-audiobook-worker-1.hf.space/health
```

### Q: Dockerfile 本身更新了怎么办

Dockerfile 是唯一需要手动同步到 HF Space 的文件。重复首次部署的第 2-4 步即可（只需覆盖 Dockerfile）。

### Q: 多个 Worker 如何更新

每个 Worker 只需点击各自的"重新部署"按钮即可，无需操作 Git。
首次部署时，对每个 Space 重复首次部署步骤。

---

## 快速参考卡片

```
┌─────────────────────────────────────────────────────┐
│  首次部署（仅一次）                                   │
│  1. git clone HF Space 仓库                         │
│  2. 复制 Dockerfile 到仓库根目录                     │
│  3. 配置 Secrets（HF_TOKEN 等）                      │
│  4. git push → 自动构建                              │
│                                                     │
│  日常更新                                           │
│  1. git push 到 GitHub                              │
│  2. 打开面板 → 点击 🔄 重新部署                      │
│  3. 等待构建完成（5-10 分钟）                        │
│  4. curl /health 验证                               │
└─────────────────────────────────────────────────────┘
```
