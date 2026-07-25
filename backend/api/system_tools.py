"""系统工具 API — 备份/恢复/导出的 Web 管理（Python 原生，无需 docker exec）。"""

from __future__ import annotations

import os
import sys
import gzip
import shutil
import tarfile
import subprocess
import threading
import logging
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system-tools", tags=["系统工具"])

# ─── 路径 ───
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", str(_PROJECT_ROOT / "backups")))
_BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# ─── 数据库连接信息（从 DATABASE_URL 解析）───
_dsn = os.environ.get("DATABASE_URL", "postgresql://audiobook_app:changeme@postgres:5432/audiobook")
_parsed = urlparse(_dsn)
PG_HOST = _parsed.hostname or "postgres"
PG_PORT = str(_parsed.port or 5432)
PG_USER = _parsed.username or "audiobook_app"
PG_PASSWORD = _parsed.password or ""
PG_DB = _parsed.path.lstrip("/") if _parsed.path else "audiobook"

# ─── rclone 配置 ───
RCLONE_GDRIVE = "gdrive"
RCLONE_MEGA = "mega"
RCLONE_DEST_DIR = "audiobook_backup"

# ─── 脚本执行状态（模块级，单例）───
_script_lock = threading.Lock()
_script_state: dict = {
    "running": False,
    "script_name": None,
    "started_at": None,
    "finished_at": None,
    "exit_code": None,
    "logs": [],
}
_MAX_LOG_LINES = 5000


def _pg_dump_env() -> dict:
    """返回 pg_dump 需要的环境变量。"""
    env = os.environ.copy()
    env["PGPASSWORD"] = PG_PASSWORD
    return env


def _psql_env() -> dict:
    """返回 psql 需要的环境变量。"""
    return _pg_dump_env()


def _check_not_running() -> None:
    if _script_state["running"]:
        raise HTTPException(
            status_code=409,
            detail=f"正在执行 {_script_state['script_name']}，请等待完成后重试",
        )


def _run_task(name: str, fn) -> None:
    """启动后台线程执行任务，输出实时追加到 _script_state。"""
    _script_state["running"] = True
    _script_state["script_name"] = name
    _script_state["started_at"] = datetime.now().isoformat()
    _script_state["finished_at"] = None
    _script_state["exit_code"] = None
    _script_state["logs"] = []

    def _worker():
        try:
            fn()
            _script_state["exit_code"] = 0
        except Exception as e:
            _script_state["logs"].append(f"[ERROR] {e}")
            _script_state["exit_code"] = 1
            logger.exception("系统工具执行失败: %s", name)
        finally:
            _script_state["running"] = False
            _script_state["finished_at"] = datetime.now().isoformat()
            _script_state["script_name"] = None

    thread = threading.Thread(target=_worker, daemon=True, name=f"tool-{name}")
    thread.start()


def _log(msg: str) -> None:
    _script_state["logs"].append(msg)
    if len(_script_state["logs"]) > _MAX_LOG_LINES:
        _script_state["logs"] = _script_state["logs"][-_MAX_LOG_LINES:]


def _run_pg_dump(schema_only: bool = False) -> Path:
    """执行 pg_dump，返回生成的 .sql.gz 文件路径。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = "audiobook_backup"
    suffix = ".sql.gz"
    outfile = _BACKUP_DIR / f"{prefix}_{timestamp}{suffix}"

    cmd = [
        "pg_dump",
        "-h", PG_HOST,
        "-p", PG_PORT,
        "-U", PG_USER,
        "-d", PG_DB,
        "--no-owner",
        "--no-privileges",
    ]
    if schema_only:
        cmd.append("--schema-only")

    _log(f"执行: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_pg_dump_env(),
    )
    with gzip.open(outfile, "wb") as f:
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            f.write(chunk)
    proc.wait()

    stderr_output = proc.stderr.read().decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        outfile.unlink(missing_ok=True)
        raise RuntimeError(f"pg_dump 失败 (exit {proc.returncode}): {stderr_output}")
    if stderr_output:
        _log(f"pg_dump stderr: {stderr_output}")

    size_mb = round(outfile.stat().st_size / (1024 * 1024), 2)
    _log(f"备份完成: {outfile.name} ({size_mb} MB)")
    return outfile


def _rclone_copy(src: str, dst: str) -> bool:
    """执行 rclone copy，返回是否成功。"""
    if not shutil.which("rclone"):
        _log("[WARN] rclone 未安装，跳过云盘上传")
        return False
    result = subprocess.run(
        ["rclone", "copy", src, dst, "--log-level", "ERROR"],
        capture_output=True, text=True, timeout=300,
        env={**os.environ, "HOME": os.environ.get("HOME", "/root")},
    )
    if result.returncode != 0:
        _log(f"[WARN] rclone 上传失败: {result.stderr.strip()}")
        return False
    return True


def _rclone_lsf(remote: str) -> list[str]:
    """列出云端目录内容。"""
    if not shutil.which("rclone"):
        raise RuntimeError("rclone 未安装")
    result = subprocess.run(
        ["rclone", "lsf", f"{remote}:{RCLONE_DEST_DIR}/", "--log-level", "ERROR"],
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "HOME": os.environ.get("HOME", "/root")},
    )
    if result.returncode != 0:
        raise RuntimeError(f"rclone 失败: {result.stderr.strip()}")
    return [line.strip() for line in result.stdout.split("\n") if line.strip()]


def _rclone_deletefile(remote: str, filename: str) -> None:
    """删除云端文件。"""
    subprocess.run(
        ["rclone", "deletefile", f"{remote}:{RCLONE_DEST_DIR}/{filename}", "--log-level", "ERROR"],
        capture_output=True, timeout=30,
        env={**os.environ, "HOME": os.environ.get("HOME", "/root")},
    )


def _cleanup_old_backups(keep: int = 7) -> None:
    """保留最新 keep 个备份，删除其余。"""
    backups = sorted(_BACKUP_DIR.glob("audiobook_backup_*.sql.gz"),
                     key=lambda x: x.stat().st_mtime, reverse=True)
    for old in backups[keep:]:
        old.unlink()
        _log(f"已删除旧备份: {old.name}")
    if len(backups) > keep:
        _log(f"保留最新 {keep} 个，删除 {len(backups) - keep} 个旧备份")
    else:
        _log(f"当前 {len(backups)} 个备份，无需清理")


def _cleanup_cloud_old(remote: str, keep: int = 7) -> None:
    """清理云端旧备份。"""
    try:
        files = _rclone_lsf(remote)
        backups = sorted(
            [f for f in files if "audiobook_backup_" in f and f.endswith(".sql.gz")],
            reverse=True,
        )
        for old in backups[keep:]:
            _rclone_deletefile(remote, old)
            _log(f"云端已删除: {old}")
    except Exception as e:
        _log(f"[WARN] 清理云端旧备份失败: {e}")


# ============================================================
# 状态查询
# ============================================================

@router.get("/status")
def get_status():
    return {
        "running": _script_state["running"],
        "script_name": _script_state["script_name"],
        "started_at": _script_state["started_at"],
        "finished_at": _script_state["finished_at"],
        "exit_code": _script_state["exit_code"],
        "logs": _script_state["logs"],
        "log_count": len(_script_state["logs"]),
    }


# ============================================================
# 备份管理
# ============================================================

def _list_files(directory: Path, pattern: str) -> list[dict]:
    result = []
    if not directory.exists():
        return result
    for f in sorted(directory.glob(pattern), key=lambda x: x.stat().st_mtime, reverse=True):
        stat = f.stat()
        result.append({
            "name": f.name,
            "size_bytes": stat.st_size,
            "size_mb": round(stat.st_size / (1024 * 1024), 2),
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })
    return result


@router.get("/backups")
def list_backups():
    return {"backups": _list_files(_BACKUP_DIR, "audiobook_backup_*.sql.gz")}


@router.post("/backup")
def create_backup():
    """本地备份。"""
    _check_not_running()
    with _script_lock:
        _check_not_running()

    def _do():
        _log("=== 本地备份 ===")
        _run_pg_dump()
        # 表行数统计
        result = subprocess.run(
            ["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", PG_DB,
             "-t", "-c", "SELECT relname||' = '||n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup DESC;"],
            capture_output=True, text=True, env=_psql_env(),
        )
        if result.returncode == 0:
            _log("表行数统计:")
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    _log(f"  {line.strip()}")
        _cleanup_old_backups(7)
        _log("=== 完成 ===")

    _run_task("本地备份", _do)
    return {"success": True, "message": "本地备份已启动"}


@router.post("/backup/cloud")
def create_cloud_backup():
    """云盘备份（pg_dump + 上传 Google Drive + Mega + 备份 .env）。"""
    _check_not_running()
    with _script_lock:
        _check_not_running()

    def _do():
        _log("=== 云盘备份 ===")
        # 1. 数据库备份
        backup_file = _run_pg_dump()

        # 2. 备份 .env
        env_path = Path("/app/.env")
        if env_path.exists():
            env_backup = _BACKUP_DIR / "env_backup"
            shutil.copy2(env_path, env_backup)
            _log(".env 已备份")

        # 3. 上传到云盘
        for remote_name in [RCLONE_GDRIVE, RCLONE_MEGA]:
            _log(f"上传到 {remote_name}...")
            remote_path = f"{remote_name}:{RCLONE_DEST_DIR}/"
            ok = _rclone_copy(str(backup_file), remote_path)
            if ok:
                _log(f"{remote_name} 上传完成")
                # 上传 .env
                env_backup = _BACKUP_DIR / "env_backup"
                if env_backup.exists():
                    _rclone_copy(str(env_backup), remote_path)
                # 清理云端旧备份
                _cleanup_cloud_old(remote_name, 7)
            else:
                _log(f"[WARN] {remote_name} 上传失败")

        # 4. 清理本地旧备份
        _cleanup_old_backups(7)
        _log("=== 完成 ===")

    _run_task("云盘备份", _do)
    return {"success": True, "message": "云盘备份已启动"}


@router.delete("/backup/{filename}")
def delete_backup(filename: str):
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="无效的文件名")
    filepath = _BACKUP_DIR / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="备份文件不存在")
    filepath.unlink()
    return {"success": True, "message": f"已删除 {filename}"}


# ============================================================
# 云盘测试
# ============================================================

@router.post("/cloud/test")
def cloud_test():
    """云盘连通性测试。"""
    _check_not_running()
    with _script_lock:
        _check_not_running()

    def _do():
        _log("=== 云盘连通性测试 ===")

        # 1. 小型备份测试（仅表结构）
        _log("备份表结构...")
        try:
            test_file = _run_pg_dump(schema_only=True)
            _log(f"备份成功: {test_file.name} ({round(test_file.stat().st_size / 1024, 1)} KB)")
        except Exception as e:
            _log(f"[FAIL] 备份失败: {e}")
            _log("=== 测试失败 ===")
            return

        # 2. 测试各云盘
        for remote_name in [RCLONE_GDRIVE, RCLONE_MEGA]:
            _log(f"测试 {remote_name} 上传...")
            if not shutil.which("rclone"):
                _log(f"[FAIL] rclone 未安装")
                continue
            # 检查远程是否配置
            chk = subprocess.run(
                ["rclone", "listremotes"],
                capture_output=True, text=True, timeout=5,
                env={**os.environ, "HOME": os.environ.get("HOME", "/root")},
            )
            if f"{remote_name}:" not in chk.stdout:
                _log(f"[FAIL] 远程 '{remote_name}' 未配置")
                continue

            ok = _rclone_copy(str(test_file), f"{remote_name}:{RCLONE_DEST_DIR}/")
            if ok:
                _log(f"[OK] {remote_name} 上传成功")
                _rclone_deletefile(remote_name, test_file.name)
                _log(f"已清理测试文件")
            else:
                _log(f"[FAIL] {remote_name} 上传失败")

        # 3. 清理本地测试文件
        test_file.unlink(missing_ok=True)
        _log("本地测试文件已清理")
        _log("=== 测试完成 ===")

    _run_task("云盘测试", _do)
    return {"success": True, "message": "云盘连通性测试已启动"}


# ============================================================
# 云端备份列表
# ============================================================

@router.get("/cloud/backups")
def list_cloud_backups(source: str = "gdrive"):
    remote_map = {"gdrive": RCLONE_GDRIVE, "mega": RCLONE_MEGA}
    remote = remote_map.get(source)
    if not remote:
        raise HTTPException(status_code=400, detail="不支持的源，可选 gdrive 或 mega")
    try:
        files = _rclone_lsf(remote)
        backups = [
            {"name": f, "source": source}
            for f in files
            if "audiobook_backup_" in f and f.endswith(".sql.gz")
        ]
        return {"backups": backups, "source": source}
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=504, detail=f"rclone 超时或失败: {e}")


# ============================================================
# 恢复
# ============================================================

class RestoreRequest(BaseModel):
    confirm: bool = False


@router.post("/restore/{filename}")
def restore_backup(filename: str, body: RestoreRequest):
    """从本地备份恢复数据库。"""
    if not body.confirm:
        raise HTTPException(status_code=400, detail="请确认恢复操作（confirm=true）")
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="无效的文件名")
    filepath = _BACKUP_DIR / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="备份文件不存在")

    _check_not_running()
    with _script_lock:
        _check_not_running()

    def _do():
        _log(f"=== 恢复数据库: {filename} ===")
        _log("断开数据库连接...")
        subprocess.run(
            ["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", "postgres",
             "-c", f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{PG_DB}' AND pid <> pg_backend_pid();"],
            capture_output=True, env=_psql_env(),
        )
        _log("重建数据库...")
        subprocess.run(
            ["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", "postgres",
             "-c", f"DROP DATABASE IF EXISTS {PG_DB};"],
            capture_output=True, env=_psql_env(), check=True,
        )
        subprocess.run(
            ["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", "postgres",
             "-c", f"CREATE DATABASE {PG_DB};"],
            capture_output=True, env=_psql_env(), check=True,
        )
        _log("导入数据...")
        with gzip.open(filepath, "rb") as gz:
            proc = subprocess.run(
                ["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", PG_DB],
                stdin=gz, capture_output=True, env=_psql_env(),
            )
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace")
            _log(f"[WARN] 恢复过程有警告: {stderr[:500]}")
        _log("更新统计信息...")
        subprocess.run(
            ["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", PG_DB, "-c", "ANALYZE;"],
            capture_output=True, env=_psql_env(),
        )
        # 验证表行数
        result = subprocess.run(
            ["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", PG_DB,
             "-t", "-c", "SELECT relname||' = '||n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup DESC;"],
            capture_output=True, text=True, env=_psql_env(),
        )
        if result.returncode == 0:
            _log("表行数统计:")
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    _log(f"  {line.strip()}")
        _log("=== 恢复完成 ===")
        _log("建议重启 Web 服务: docker-compose restart web")

    _run_task("数据库恢复", _do)
    return {"success": True, "message": f"正在从 {filename} 恢复数据库"}


class CloudRestoreRequest(BaseModel):
    confirm: bool = False
    source: str = "gdrive"
    filename: str | None = None


@router.post("/restore/cloud")
def restore_from_cloud(body: CloudRestoreRequest):
    """从云盘恢复数据库。"""
    if not body.confirm:
        raise HTTPException(status_code=400, detail="请确认恢复操作（confirm=true）")

    remote_map = {"gdrive": RCLONE_GDRIVE, "mega": RCLONE_MEGA}
    remote = remote_map.get(body.source)
    if not remote:
        raise HTTPException(status_code=400, detail="不支持的源")

    # 如果指定了文件名，先下载
    target_file = body.filename
    if not target_file:
        # 获取最新备份
        try:
            files = _rclone_lsf(remote)
            backups = sorted(
                [f for f in files if "audiobook_backup_" in f and f.endswith(".sql.gz")],
                reverse=True,
            )
            if not backups:
                raise HTTPException(status_code=404, detail="云端无备份文件")
            target_file = backups[0]
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    _check_not_running()
    with _script_lock:
        _check_not_running()

    def _do():
        _log(f"=== 云盘恢复: {body.source} / {target_file} ===")
        # 1. 下载备份文件
        _log(f"从 {body.source} 下载...")
        if not shutil.which("rclone"):
            raise RuntimeError("rclone 未安装")
        download_file = _BACKUP_DIR / target_file
        result = subprocess.run(
            ["rclone", "copy", f"{remote}:{RCLONE_DEST_DIR}/{target_file}", str(_BACKUP_DIR) + "/",
             "--log-level", "INFO", "--progress"],
            capture_output=True, text=True, timeout=600,
            env={**os.environ, "HOME": os.environ.get("HOME", "/root")},
        )
        if result.returncode != 0 or not download_file.exists():
            raise RuntimeError(f"下载失败: {result.stderr[:500]}")
        size_mb = round(download_file.stat().st_size / (1024 * 1024), 2)
        _log(f"下载完成: {target_file} ({size_mb} MB)")

        # 2. 恢复数据库
        _log("断开数据库连接...")
        subprocess.run(
            ["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", "postgres",
             "-c", f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{PG_DB}' AND pid <> pg_backend_pid();"],
            capture_output=True, env=_psql_env(),
        )
        _log("重建数据库...")
        subprocess.run(
            ["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", "postgres",
             "-c", f"DROP DATABASE IF EXISTS {PG_DB};"],
            capture_output=True, env=_psql_env(), check=True,
        )
        subprocess.run(
            ["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", "postgres",
             "-c", f"CREATE DATABASE {PG_DB};"],
            capture_output=True, env=_psql_env(), check=True,
        )
        _log("导入数据...")
        with gzip.open(download_file, "rb") as gz:
            proc = subprocess.run(
                ["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", PG_DB],
                stdin=gz, capture_output=True, env=_psql_env(),
            )
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace")
            _log(f"[WARN] 恢复过程有警告: {stderr[:500]}")
        _log("更新统计信息...")
        subprocess.run(
            ["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", PG_DB, "-c", "ANALYZE;"],
            capture_output=True, env=_psql_env(),
        )
        _log("=== 恢复完成 ===")
        _log("建议重启 Web 服务: docker-compose restart web")

    _run_task("云盘恢复", _do)
    return {"success": True, "message": f"正在从 {body.source} 恢复数据库"}


# ============================================================
# 项目导出
# ============================================================

@router.post("/export")
def export_project():
    """导出项目迁移包（数据库 + .env + relay 配置）。"""
    _check_not_running()
    with _script_lock:
        _check_not_running()

    def _do():
        _log("=== 项目导出 ===")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        tmp_dir = _BACKUP_DIR / f"_export_{timestamp}"
        tmp_dir.mkdir(exist_ok=True)

        try:
            # 1. 数据库备份
            _log("备份数据库...")
            db_file = tmp_dir / "db_backup.sql.gz"
            cmd = [
                "pg_dump", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", PG_DB,
                "--no-owner", "--no-privileges",
            ]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_pg_dump_env())
            with gzip.open(db_file, "wb") as f:
                while True:
                    chunk = proc.stdout.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
            proc.wait()
            if proc.returncode != 0:
                raise RuntimeError(f"pg_dump 失败: {proc.stderr.read().decode('utf-8', errors='replace')[:300]}")
            _log(f"数据库备份完成: {round(db_file.stat().st_size / (1024*1024), 2)} MB")

            # 2. .env 备份
            env_path = Path("/app/.env")
            if env_path.exists():
                shutil.copy2(env_path, tmp_dir / "env_backup")
                _log(".env 已备份")

            # 3. 表行数统计
            result = subprocess.run(
                ["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", PG_DB,
                 "-t", "-c", "SELECT relname||' = '||n_live_tup FROM pg_stat_user_tables ORDER BY relname;"],
                capture_output=True, text=True, env=_psql_env(),
            )
            counts_file = tmp_dir / "table_counts.txt"
            if result.returncode == 0:
                counts_file.write_text(result.stdout)

            # 4. 打包
            bundle_file = _BACKUP_DIR / f"migration_bundle_{timestamp}.tar.gz"
            _log("打包迁移包...")
            with tarfile.open(bundle_file, "w:gz") as tar:
                for item in tmp_dir.iterdir():
                    tar.add(item, arcname=item.name)
            bundle_mb = round(bundle_file.stat().st_size / (1024 * 1024), 2)
            _log(f"迁移包: {bundle_file.name} ({bundle_mb} MB)")

            # 5. 清理临时目录
            shutil.rmtree(tmp_dir, ignore_errors=True)
            _log("=== 导出完成 ===")

        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

    _run_task("项目导出", _do)
    return {"success": True, "message": "项目导出已启动"}


@router.get("/exports")
def list_exports():
    """列出本地迁移包。"""
    return {"bundles": _list_files(_BACKUP_DIR, "migration_bundle_*.tar.gz")}


# ============================================================
# rclone 检测
# ============================================================

@router.get("/rclone-info")
def rclone_info():
    """检查 rclone 安装和远程配置状态。"""
    installed = shutil.which("rclone") is not None
    remotes = []
    if installed:
        result = subprocess.run(
            ["rclone", "listremotes"],
            capture_output=True, text=True, timeout=5,
            env={**os.environ, "HOME": os.environ.get("HOME", "/root")},
        )
        if result.returncode == 0:
            remotes = [line.strip().rstrip(":") for line in result.stdout.split("\n") if line.strip()]
    return {
        "installed": installed,
        "remotes": remotes,
        "gdrive_configured": "gdrive" in remotes,
        "mega_configured": "mega" in remotes,
    }
