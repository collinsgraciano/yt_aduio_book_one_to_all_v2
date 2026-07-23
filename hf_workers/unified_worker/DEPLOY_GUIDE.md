# HF Space 代码更新指南

> 本文档介绍如何**删除 Hugging Face Space 上的旧代码并上传新代码**。
> 适用于 pipeline 更新、BGM 逻辑修改、Worker 代码迭代等场景。

---

## 前置条件

- 已安装 `git`（≥ 2.0）
- 已安装 `git-lfs`（HF Space 要求，用于大文件追踪）
- 拥有 Hugging Face 账号，且已创建好 Space
- 知道自己的 HF 用户名和 Space 名称

---

## 方法一：清空重建（推荐，最干净）

> 适用于代码结构有较大变动、文件增删较多的情况。
> 核心思路：**清空 HF Space 仓库 → 重新上传全部文件**。

### 第 1 步：安装并配置 git-lfs

```bash
# Windows (PowerShell)
winget install GitHub.GitLFS

# macOS / Linux
brew install git-lfs   # macOS
sudo apt install git-lfs  # Debian/Ubuntu

# 初始化（仅需执行一次）
git lfs install
```

### 第 2 步：克隆 HF Space 仓库到本地

```bash
# 替换为你的用户名和 Space 名称
git clone https://huggingface.co/spaces/你的用户名/audiobook-worker-1

cd audiobook-worker-1
```

> 如果提示输入密码，使用 HF Access Token（不是账号密码）。
> Token 获取：[huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) → New token → Role 选 Write。

### 第 3 步：清空旧代码

```bash
# 删除所有已跟踪文件（保留 .git 目录）
# Windows (PowerShell)
Get-ChildItem -Exclude .git | Remove-Item -Recurse -Force

# macOS / Linux
find . -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf {} +
```

### 第 4 步：从项目复制最新文件

回到项目根目录，将以下文件复制到 HF Space 仓库中：

```bash
# 以下命令假设：
#   PROJECT_ROOT = H:\2026_main_project\yt_aduio_book_one_to_all_v2
#   HF_SPACE    = 你克隆的 audiobook-worker-1 目录路径

# Windows (PowerShell) — 从项目根目录执行
$PROJECT = "H:\2026_main_project\yt_aduio_book_one_to_all_v2"
$SPACE = "你的audiobook-worker-1路径"

# 复制 unified_worker 文件
Copy-Item "$PROJECT\hf_workers\unified_worker\app.py"        "$SPACE\app.py"
Copy-Item "$PROJECT\hf_workers\unified_worker\runner.py"      "$SPACE\runner.py"
Copy-Item "$PROJECT\hf_workers\unified_worker\shared.py"       "$SPACE\shared.py"
Copy-Item "$PROJECT\hf_workers\unified_worker\requirements.txt" "$SPACE\requirements.txt"
Copy-Item "$PROJECT\hf_workers\unified_worker\Dockerfile"      "$SPACE\Dockerfile"

# pipeline/ 无需复制 — 构建时从 GitHub 自动拉取
```

```bash
# macOS / Linux — 从项目根目录执行
PROJECT="/path/to/yt_aduio_book_one_to_all_v2"
SPACE="/path/to/audiobook-worker-1"

cp "$PROJECT/hf_workers/unified_worker/app.py"        "$SPACE/app.py"
cp "$PROJECT/hf_workers/unified_worker/runner.py"    "$SPACE/runner.py"
cp "$PROJECT/hf_workers/unified_worker/shared.py"    "$SPACE/shared.py"
cp "$PROJECT/hf_workers/unified_worker/requirements.txt" "$SPACE/requirements.txt"
cp "$PROJECT/hf_workers/unified_worker/Dockerfile"   "$SPACE/Dockerfile"

# pipeline/ 无需复制 — 构建时从 GitHub 自动拉取
```

### 第 5 步：清理不需要的文件

```bash
# 删除可能残留的 __pycache__ 目录
# Windows (PowerShell)
Get-ChildItem -Path "$SPACE" -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# macOS / Linux
find "$SPACE" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
```

### 第 6 步：提交并推送

```bash
cd "你的audiobook-worker-1路径"

# 添加所有文件（包括删除）
git add -A

# 查看变更概况
git status

# 提交
git commit -m "更新 Worker 代码: BGM 侧链压缩 + pipeline 同步"

# 推送到 HF Space（会自动触发重新构建）
git push origin main
```

> 推送后 HF Space 会自动开始构建。访问 Space 页面可查看构建日志。
> 构建时间约 5-10 分钟（取决于 DeepFilter 下载速度）。

---

## 方法二：增量更新（适用于小改动）

> 适用于只改了几个文件的情况（如只更新了 `runner.py`）。
> 不需要清空仓库，直接覆盖变更的文件。
> pipeline 改动直接 push GitHub，无需同步到 HF Space。

### 第 1 步：克隆 HF Space 仓库（如果尚未克隆）

```bash
git clone https://huggingface.co/spaces/你的用户名/audiobook-worker-1
cd audiobook-worker-1
```

### 第 2 步：覆盖变更的文件

根据实际改动，选择性地复制文件：

```bash
# Windows (PowerShell)
$PROJECT = "H:\2026_main_project\yt_aduio_book_one_to_all_v2"
$SPACE = "你的audiobook-worker-1路径"

# 例：只更新了 BGM 侧链压缩相关代码
Copy-Item "$PROJECT\hf_workers\unified_worker\runner.py"  "$SPACE\runner.py" -Force
```

```bash
# macOS / Linux
PROJECT="/path/to/yt_aduio_book_one_to_all_v2"
SPACE="/path/to/audiobook-worker-1"

cp "$PROJECT/hf_workers/unified_worker/runner.py" "$SPACE/runner.py"
```

### 第 3 步：提交并推送

```bash
git add -A
git commit -m "更新 BGM 侧链压缩逻辑"
git push origin main
```

---

## 方法三：通过 HF Web 界面操作（无需命令行）

> 适用于不熟悉 Git 的用户，或只需替换少量文件的场景。

### 删除文件

1. 打开 HF Space 页面：`https://huggingface.co/spaces/你的用户名/audiobook-worker-1`
2. 点击 **Files** 标签
3. 找到要删除的文件，点击右侧 **⋯** → **Delete**
4. 弹窗确认删除

### 上传文件

1. 在 Files 页面点击 **Add file** → **Upload file**
2. 拖拽或选择要上传的文件
3. 填写 commit message，点击 **Commit changes**

### 批量替换整个目录

> HF Web 界面不支持上传文件夹，只能逐个上传文件。
> 如果需要批量替换 `pipeline/` 目录，建议使用方法一或方法二。

---

## 需要同步的文件清单

当项目代码更新时，根据改动范围同步对应文件到 HF Space：

| 改动范围 | 需要同步的文件 | 说明 |
|----------|---------------|------|
| Worker 应用 | `app.py` | 端点、路由、槽位管理 |
| 测试执行器 | `runner.py` | HF Worker 测试端点参数 |
| 共享工具 | `shared.py` | Worker 与 VPS 中继共享逻辑 |
| 依赖变更 | `requirements.txt` | 新增/移除 Python 包 |
| Docker 变更 | `Dockerfile` | 系统依赖、构建步骤 |

> **注意**：`pipeline/` 目录无需同步，构建时自动从 GitHub 拉取最新代码。
> 如果 pipeline 有改动，先 push 到 GitHub，HF Space 下次构建时自动生效。
> **建议**：不确定改了哪些文件时，用方法一清空重建最保险。

---

## 验证更新成功

### 1. 检查构建状态

推送后访问 HF Space 页面，查看右上角构建状态：
- 🟡 **Building** — 正在构建，等待完成
- 🟢 **Running** — 构建成功，已启动
- 🔴 **Error** — 构建失败，点击 Logs 查看错误

### 2. 健康检查

构建完成后，验证 Worker 是否正常运行：

```bash
# 替换为你的 HF Space 地址
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

### 3. 验证 BGM 侧链压缩参数

通过 BGM 测试页面或 API 验证新参数是否生效：

```bash
# 检查 BGM 下载状态和音乐池
curl https://你的用户名-audiobook-worker-1.hf.space/bgm-status
```

---

## 常见问题

### Q: 推送时提示 `Large files detected`

HF Space 要求大文件通过 Git LFS 管理。如果 `pipeline/` 中有大文件：

```bash
# 在 HF Space 仓库中创建 .gitattributes
echo "*.pt filter=lfs diff=lfs merge=lfs -text" > .gitattributes
echo "*.bin filter=lfs diff=lfs merge=lfs -text" >> .gitattributes
echo "*.wav filter=lfs diff=lfs merge=lfs -text" >> .gitattributes
git add .gitattributes
git commit -m "Add LFS rules"
git push
```

### Q: 构建失败 `COPY failed: file not found`

确保 HF Space 仓库根目录包含以下文件：`app.py`、`runner.py`、`shared.py`、`requirements.txt`、`Dockerfile`。`pipeline/` 无需上传，构建时自动从 GitHub 拉取。

### Q: 推送后没有触发重新构建

确认推送到了 `main` 分支：
```bash
git branch
# 应显示 * main
```

### Q: 构建成功但 /health 返回 502

HF Space 冷启动需要 30-60 秒。等待片刻后重试：
```bash
# 等待 30 秒后重试
sleep 30 && curl https://你的用户名-audiobook-worker-1.hf.space/health
```

### Q: pipeline/ 目录中有 __pycache__ 导致体积过大

`pipeline/` 现在从 GitHub 自动拉取，HF Space 仓库中不包含 pipeline 目录，无需清理。

### Q: 多个 Worker 如何批量更新

对每个 HF Space 仓库重复上述步骤。或者用脚本批量推送：

```bash
#!/bin/bash
# 批量更新所有 Worker
WORKERS=("audiobook-worker-1" "audiobook-worker-2" "audiobook-worker-3")
HF_USER="你的用户名"
PROJECT="/path/to/yt_aduio_book_one_to_all_v2"

for w in "${WORKERS[@]}"; do
    echo "=== 更新 $w ==="
    rm -rf "/tmp/$w"
    git clone "https://huggingface.co/spaces/$HF_USER/$w" "/tmp/$w"
    cd "/tmp/$w"

    # 清空旧代码
    find . -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf {} +

    # 复制最新文件
    cp "$PROJECT/hf_workers/unified_worker/app.py" .
    cp "$PROJECT/hf_workers/unified_worker/runner.py" .
    cp "$PROJECT/hf_workers/unified_worker/shared.py" .
    cp "$PROJECT/hf_workers/unified_worker/requirements.txt" .
    cp "$PROJECT/hf_workers/unified_worker/Dockerfile" .

    # 清理缓存（如有残留）
    find ./pipeline -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

    # 提交推送
    git add -A
    git commit -m "批量更新 Worker 代码"
    git push origin main

    cd ..
done
```

---

## 快速参考卡片

```
┌─────────────────────────────────────────────────────┐
│  HF Space 代码更新流程                                │
│                                                     │
│  1. git clone HF Space 仓库                         │
│  2. 清空旧文件（保留 .git）                          │
│  3. 从项目复制最新文件                                │
│     - app.py / runner.py / shared.py               │
│     - requirements.txt / Dockerfile                 │
│  4. 清理 __pycache__（如有）                         │
│  5. git add -A && git commit && git push           │
│  6. 等待 HF 构建完成（5-10 分钟）                    │
│     pipeline/ 自动从 GitHub 拉取                     │
│  7. curl /health 验证                               │
└─────────────────────────────────────────────────────┘
```
