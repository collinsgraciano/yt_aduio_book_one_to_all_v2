# HF Space 部署指南

> Worker 的全部代码（pipeline + app.py + runner.py + shared.py + requirements.txt）
> 在容器启动时自动从 GitHub 拉取，HF Space 仓库中**只需一个 Dockerfile**。
> 重新部署无需 HF_TOKEN，点击按钮即可。

---

## 前置条件

- 已安装 `git`（≥ 2.0）
- 已安装 `git-lfs`（HF Space 要求，用于大文件追踪）
- 拥有 Hugging Face 账号，且已创建好 Space
- 知道自己的 HF 用户名和 Space 名称

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

## 日常更新

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
点击 **🔄 重新部署** 按钮。

> 原理：按钮让容器退出 → HF Space 自动重启 → 启动脚本从 GitHub 拉取最新代码 → 启动应用。
> 无需 HF_TOKEN，无需调用 HF API。
>
> 也可以调用 API：`POST /redeploy`

---

## 验证部署

### 1. 健康检查

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

### 2. BGM 状态

```bash
curl https://你的用户名-audiobook-worker-1.hf.space/bgm-status
```

---

## 常见问题

### Q: 构建失败 `wget: unable to resolve host address`

HF Space 构建环境无法访问 GitHub。检查网络或稍后重试。

### Q: 重新部署后代码没更新

确认已先 `git push` 到 GitHub。重新部署时 start.sh 从 GitHub main 分支拉取代码。

### Q: 构建成功但 /health 返回 502

容器重启后需要时间拉取代码和启动应用，等待 30-60 秒后重试：
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
│  3. 配置 Secrets（POSTGRES_DSN 等）                  │
│  4. git push → 自动构建                              │
│                                                     │
│  日常更新                                           │
│  1. git push 到 GitHub                              │
│  2. 打开面板 → 点击 🔄 重新部署                      │
│  3. 等待重启完成（30-60 秒）                         │
│  4. curl /health 验证                               │
└─────────────────────────────────────────────────────┘
```
