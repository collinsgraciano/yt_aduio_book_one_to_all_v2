"""系统工具 API — 备份/恢复/导出/定时任务的 Web 管理（Python 原生，无需 docker exec）。"""

from __future__ import annotations

import os
import json
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
_SCHEDULE_FILE = _BACKUP_DIR / "schedule.json"

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

# ─── 保留数量 ───
KEEP_COUNT = 3

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


# ============================================================
# 通用工具函数
# ============================================================

def _pg_dump_env() -> dict:
    env = os.environ.copy()
    env["PGPASSWORD"] = PG_PASSWORD
    return env


def _psql_env() -> dict:
    return _pg_dump_env()


def _rclone_env() -> dict:
    return {**os.environ, "HOME": os.environ.get("HOME", "/root")}


def _check_not_running() -> None:
    if _script_state["running"]:
        raise HTTPException(
            status_code=409,
            detail=f"正在执行 {_script_state['script_name']}，请等待完成后重试",
        )


def _log(msg: str) -> None:
    _script_state["logs"].append(msg)
    if len(_script_state["logs"]) > _MAX_LOG_LINES:
        _script_state["logs"] = _script_state["logs"][-_MAX_LOG_LINES:]


def _run_task(name: str, fn) -> None:
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


def _run_pg_dump(schema_only: bool = False) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    outfile = _BACKUP_DIR / f"audiobook_backup_{timestamp}.sql.gz"
    cmd = ["pg_dump", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", PG_DB,
           "--no-owner", "--no-privileges"]
    if schema_only:
        cmd.append("--schema-only")
    _log(f"执行: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_pg_dump_env())
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
    if not shutil.which("rclone"):
        _log("[WARN] rclone 未安装，跳过云盘上传")
        return False
    result = subprocess.run(
        ["rclone", "copy", src, dst, "--log-level", "ERROR"],
        capture_output=True, text=True, timeout=300, env=_rclone_env(),
    )
    if result.returncode != 0:
        _log(f"[WARN] rclone 上传失败: {result.stderr.strip()}")
        return False
    return True


def _rclone_lsf(remote: str) -> list[str]:
    if not shutil.which("rclone"):
        raise RuntimeError("rclone 未安装")
    result = subprocess.run(
        ["rclone", "lsf", f"{remote}:{RCLONE_DEST_DIR}/", "--log-level", "ERROR"],
        capture_output=True, text=True, timeout=30, env=_rclone_env(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"rclone 失败: {result.stderr.strip()}")
    return [line.strip() for line in result.stdout.split("\n") if line.strip()]


def _rclone_deletefile(remote: str, filename: str) -> None:
    subprocess.run(
        ["rclone", "deletefile", f"{remote}:{RCLONE_DEST_DIR}/{filename}", "--log-level", "ERROR"],
        capture_output=True, timeout=30, env=_rclone_env(),
    )


def _cleanup_old(pattern: str, keep: int = KEEP_COUNT) -> None:
    files = sorted(_BACKUP_DIR.glob(pattern), key=lambda x: x.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        old.unlink()
        _log(f"已删除旧文件: {old.name}")
    if len(files) > keep:
        _log(f"保留最新 {keep} 个，删除 {len(files) - keep} 个旧文件")


def _cleanup_cloud_old(remote: str, pattern: str = "audiobook_backup_", keep: int = KEEP_COUNT) -> None:
    try:
        files = _rclone_lsf(remote)
        backups = sorted(
            [f for f in files if pattern in f and f.endswith(".sql.gz")],
            reverse=True,
        )
        for old in backups[keep:]:
            _rclone_deletefile(remote, old)
            _log(f"云端已删除: {old}")
    except Exception as e:
        _log(f"[WARN] 清理云端旧文件失败: {e}")


def _cleanup_cloud_exports(remote: str, keep: int = KEEP_COUNT) -> None:
    try:
        files = _rclone_lsf(remote)
        bundles = sorted(
            [f for f in files if "migration_bundle_" in f and f.endswith(".tar.gz")],
            reverse=True,
        )
        for old in bundles[keep:]:
            _rclone_deletefile(remote, old)
            _log(f"云端已删除: {old}")
    except Exception as e:
        _log(f"[WARN] 清理云端迁移包失败: {e}")


def _upload_to_clouds(filepath: Path, delete_after: bool = False) -> None:
    """上传文件到 gdrive + mega，并清理云端旧文件。"""
    for remote_name in [RCLONE_GDRIVE, RCLONE_MEGA]:
        _log(f"上传到 {remote_name}...")
        remote_path = f"{remote_name}:{RCLONE_DEST_DIR}/"
        ok = _rclone_copy(str(filepath), remote_path)
        if ok:
            _log(f"{remote_name} 上传完成")
        else:
            _log(f"[WARN] {remote_name} 上传失败")


def _table_counts() -> None:
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


# ============================================================
# 核心操作函数（API 和定时任务共用）
# ============================================================

def _do_backup() -> None:
    _log("=== 本地备份 ===")
    _run_pg_dump()
    _table_counts()
    _cleanup_old("audiobook_backup_*.sql.gz")
    _log("=== 完成 ===")


def _do_cloud_backup() -> None:
    _log("=== 云盘备份 ===")
    backup_file = _run_pg_dump()
    env_path = Path("/app/.env")
    if env_path.exists():
        env_backup = _BACKUP_DIR / "env_backup"
        shutil.copy2(env_path, env_backup)
        _log(".env 已备份")
    for remote_name in [RCLONE_GDRIVE, RCLONE_MEGA]:
        _log(f"上传到 {remote_name}...")
        remote_path = f"{remote_name}:{RCLONE_DEST_DIR}/"
        ok = _rclone_copy(str(backup_file), remote_path)
        if ok:
            _log(f"{remote_name} 上传完成")
            env_backup = _BACKUP_DIR / "env_backup"
            if env_backup.exists():
                _rclone_copy(str(env_backup), remote_path)
            _cleanup_cloud_old(remote_name, "audiobook_backup_")
        else:
            _log(f"[WARN] {remote_name} 上传失败")
    _cleanup_old("audiobook_backup_*.sql.gz")
    _log("=== 完成 ===")


def _do_export() -> None:
    _log("=== 项目导出 ===")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp_dir = _BACKUP_DIR / f"_export_{timestamp}"
    tmp_dir.mkdir(exist_ok=True)
    try:
        _log("备份数据库...")
        db_file = tmp_dir / "db_backup.sql.gz"
        cmd = ["pg_dump", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", PG_DB,
               "--no-owner", "--no-privileges"]
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

        env_path = Path("/app/.env")
        if env_path.exists():
            shutil.copy2(env_path, tmp_dir / "env_backup")
            _log(".env 已备份")

        result = subprocess.run(
            ["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", PG_DB,
             "-t", "-c", "SELECT relname||' = '||n_live_tup FROM pg_stat_user_tables ORDER BY relname;"],
            capture_output=True, text=True, env=_psql_env(),
        )
        if result.returncode == 0:
            (tmp_dir / "table_counts.txt").write_text(result.stdout)

        bundle_file = _BACKUP_DIR / f"migration_bundle_{timestamp}.tar.gz"
        _log("打包迁移包...")
        with tarfile.open(bundle_file, "w:gz") as tar:
            for item in tmp_dir.iterdir():
                tar.add(item, arcname=item.name)
        bundle_mb = round(bundle_file.stat().st_size / (1024 * 1024), 2)
        _log(f"迁移包: {bundle_file.name} ({bundle_mb} MB)")

        shutil.rmtree(tmp_dir, ignore_errors=True)
        _cleanup_old("migration_bundle_*.tar.gz")
        _log("=== 导出完成 ===")
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def _do_cloud_export() -> None:
    _log("=== 云盘导出 ===")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp_dir = _BACKUP_DIR / f"_export_{timestamp}"
    tmp_dir.mkdir(exist_ok=True)
    try:
        _log("备份数据库...")
        db_file = tmp_dir / "db_backup.sql.gz"
        cmd = ["pg_dump", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", PG_DB,
               "--no-owner", "--no-privileges"]
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

        env_path = Path("/app/.env")
        if env_path.exists():
            shutil.copy2(env_path, tmp_dir / "env_backup")
            _log(".env 已备份")

        result = subprocess.run(
            ["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", PG_DB,
             "-t", "-c", "SELECT relname||' = '||n_live_tup FROM pg_stat_user_tables ORDER BY relname;"],
            capture_output=True, text=True, env=_psql_env(),
        )
        if result.returncode == 0:
            (tmp_dir / "table_counts.txt").write_text(result.stdout)

        bundle_file = _BACKUP_DIR / f"migration_bundle_{timestamp}.tar.gz"
        _log("打包迁移包...")
        with tarfile.open(bundle_file, "w:gz") as tar:
            for item in tmp_dir.iterdir():
                tar.add(item, arcname=item.name)
        bundle_mb = round(bundle_file.stat().st_size / (1024 * 1024), 2)
        _log(f"迁移包: {bundle_file.name} ({bundle_mb} MB)")
        shutil.rmtree(tmp_dir, ignore_errors=True)

        # 上传到云盘
        for remote_name in [RCLONE_GDRIVE, RCLONE_MEGA]:
            _log(f"上传迁移包到 {remote_name}...")
            remote_path = f"{remote_name}:{RCLONE_DEST_DIR}/"
            ok = _rclone_copy(str(bundle_file), remote_path)
            if ok:
                _log(f"{remote_name} 上传完成")
                _cleanup_cloud_exports(remote_name)
            else:
                _log(f"[WARN] {remote_name} 上传失败")

        _cleanup_old("migration_bundle_*.tar.gz")
        _log("=== 完成 ===")
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


# ============================================================
# 定时任务
# ============================================================

_DEFAULT_SCHEDULE = {
    "backup": {"enabled": False, "interval_hours": 24, "last_run": None},
    "cloud_backup": {"enabled": False, "interval_hours": 24, "last_run": None},
    "cloud_export": {"enabled": False, "interval_hours": 168, "last_run": None},
}

_SCHEDULE_TASKS = {
    "backup": ("定时本地备份", _do_backup),
    "cloud_backup": ("定时云盘备份", _do_cloud_backup),
    "cloud_export": ("定时云盘导出", _do_cloud_export),
}

_scheduler_stop = threading.Event()


def _load_schedule() -> dict:
    if _SCHEDULE_FILE.exists():
        try:
            return json.loads(_SCHEDULE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return json.loads(json.dumps(_DEFAULT_SCHEDULE))


def _save_schedule(data: dict) -> None:
    _SCHEDULE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def start_scheduler() -> None:
    """启动定时任务调度器（在 app lifespan 中调用）。"""
    _scheduler_stop.clear()

    def _loop():
        while not _scheduler_stop.is_set():
            try:
                schedule = _load_schedule()
                now = datetime.now()
                for task_key, (task_label, task_fn) in _SCHEDULE_TASKS.items():
                    cfg = schedule.get(task_key, {})
                    if not cfg.get("enabled"):
                        continue
                    last_run_str = cfg.get("last_run")
                    if last_run_str:
                        try:
                            last_run = datetime.fromisoformat(last_run_str)
                        except Exception:
                            last_run = None
                    else:
                        last_run = None
                    interval = cfg.get("interval_hours", 24)
                    if last_run and (now - last_run).total_seconds() < interval * 3600:
                        continue
                    # 检查是否有任务在运行
                    if _script_state["running"]:
                        break
                    logger.info("定时任务触发: %s", task_label)
                    _run_task(task_label, task_fn)
                    # 更新 last_run
                    schedule = _load_schedule()
                    if task_key in schedule:
                        schedule[task_key]["last_run"] = datetime.now().isoformat()
                        _save_schedule(schedule)
                    break  # 一次循环只执行一个任务
            except Exception as e:
                logger.error("定时任务调度器异常: %s", e)
            _scheduler_stop.wait(3600)

    thread = threading.Thread(target=_loop, daemon=True, name="system-tools-scheduler")
    thread.start()
    logger.info("系统工具定时任务调度器已启动")


def stop_scheduler() -> None:
    _scheduler_stop.set()


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
    _check_not_running()
    with _script_lock:
        _check_not_running()
    _run_task("本地备份", _do_backup)
    return {"success": True, "message": "本地备份已启动"}


@router.post("/backup/cloud")
def create_cloud_backup():
    _check_not_running()
    with _script_lock:
        _check_not_running()
    _run_task("云盘备份", _do_cloud_backup)
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
    _check_not_running()
    with _script_lock:
        _check_not_running()

    def _do():
        _log("=== 云盘连通性测试 ===")
        _log("备份表结构...")
        try:
            test_file = _run_pg_dump(schema_only=True)
            _log(f"备份成功: {test_file.name} ({round(test_file.stat().st_size / 1024, 1)} KB)")
        except Exception as e:
            _log(f"[FAIL] 备份失败: {e}")
            _log("=== 测试失败 ===")
            return
        for remote_name in [RCLONE_GDRIVE, RCLONE_MEGA]:
            _log(f"测试 {remote_name} 上传...")
            if not shutil.which("rclone"):
                _log("[FAIL] rclone 未安装")
                continue
            chk = subprocess.run(["rclone", "listremotes"], capture_output=True, text=True, timeout=5, env=_rclone_env())
            if f"{remote_name}:" not in chk.stdout:
                _log(f"[FAIL] 远程 '{remote_name}' 未配置")
                continue
            ok = _rclone_copy(str(test_file), f"{remote_name}:{RCLONE_DEST_DIR}/")
            if ok:
                _log(f"[OK] {remote_name} 上传成功")
                _rclone_deletefile(remote_name, test_file.name)
                _log("已清理测试文件")
            else:
                _log(f"[FAIL] {remote_name} 上传失败")
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
        backups = [{"name": f, "source": source} for f in files if "audiobook_backup_" in f and f.endswith(".sql.gz")]
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
        subprocess.run(["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", "postgres",
                        "-c", f"DROP DATABASE IF EXISTS {PG_DB};"], capture_output=True, env=_psql_env(), check=True)
        subprocess.run(["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", "postgres",
                        "-c", f"CREATE DATABASE {PG_DB};"], capture_output=True, env=_psql_env(), check=True)
        _log("导入数据...")
        with gzip.open(filepath, "rb") as gz:
            proc = subprocess.run(
                ["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", PG_DB],
                stdin=gz, capture_output=True, env=_psql_env(),
            )
        if proc.returncode != 0:
            _log(f"[WARN] 恢复过程有警告: {proc.stderr.decode('utf-8', errors='replace')[:500]}")
        _log("更新统计信息...")
        subprocess.run(["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", PG_DB, "-c", "ANALYZE;"],
                        capture_output=True, env=_psql_env())
        _table_counts()
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
    if not body.confirm:
        raise HTTPException(status_code=400, detail="请确认恢复操作（confirm=true）")
    remote_map = {"gdrive": RCLONE_GDRIVE, "mega": RCLONE_MEGA}
    remote = remote_map.get(body.source)
    if not remote:
        raise HTTPException(status_code=400, detail="不支持的源")
    target_file = body.filename
    if not target_file:
        try:
            files = _rclone_lsf(remote)
            backups = sorted([f for f in files if "audiobook_backup_" in f and f.endswith(".sql.gz")], reverse=True)
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
        _log(f"从 {body.source} 下载...")
        if not shutil.which("rclone"):
            raise RuntimeError("rclone 未安装")
        download_file = _BACKUP_DIR / target_file
        result = subprocess.run(
            ["rclone", "copy", f"{remote}:{RCLONE_DEST_DIR}/{target_file}", str(_BACKUP_DIR) + "/",
             "--log-level", "INFO", "--progress"],
            capture_output=True, text=True, timeout=600, env=_rclone_env(),
        )
        if result.returncode != 0 or not download_file.exists():
            raise RuntimeError(f"下载失败: {result.stderr[:500]}")
        _log(f"下载完成: {target_file} ({round(download_file.stat().st_size / (1024*1024), 2)} MB)")
        _log("断开数据库连接...")
        subprocess.run(
            ["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", "postgres",
             "-c", f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{PG_DB}' AND pid <> pg_backend_pid();"],
            capture_output=True, env=_psql_env(),
        )
        _log("重建数据库...")
        subprocess.run(["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", "postgres",
                        "-c", f"DROP DATABASE IF EXISTS {PG_DB};"], capture_output=True, env=_psql_env(), check=True)
        subprocess.run(["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", "postgres",
                        "-c", f"CREATE DATABASE {PG_DB};"], capture_output=True, env=_psql_env(), check=True)
        _log("导入数据...")
        with gzip.open(download_file, "rb") as gz:
            proc = subprocess.run(
                ["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", PG_DB],
                stdin=gz, capture_output=True, env=_psql_env(),
            )
        if proc.returncode != 0:
            _log(f"[WARN] 恢复过程有警告: {proc.stderr.decode('utf-8', errors='replace')[:500]}")
        _log("更新统计信息...")
        subprocess.run(["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", PG_DB, "-c", "ANALYZE;"],
                        capture_output=True, env=_psql_env())
        _log("=== 恢复完成 ===")
        _log("建议重启 Web 服务: docker-compose restart web")

    _run_task("云盘恢复", _do)
    return {"success": True, "message": f"正在从 {body.source} 恢复数据库"}


# ============================================================
# 项目导出
# ============================================================

@router.post("/export")
def export_project():
    _check_not_running()
    with _script_lock:
        _check_not_running()
    _run_task("项目导出", _do_export)
    return {"success": True, "message": "项目导出已启动"}


@router.post("/export/cloud")
def export_cloud_project():
    """导出迁移包并上传到云盘。"""
    _check_not_running()
    with _script_lock:
        _check_not_running()
    _run_task("云盘导出", _do_cloud_export)
    return {"success": True, "message": "云盘导出已启动"}


@router.get("/exports")
def list_exports():
    return {"bundles": _list_files(_BACKUP_DIR, "migration_bundle_*.tar.gz")}


# ============================================================
# 定时任务配置
# ============================================================

@router.get("/schedule")
def get_schedule():
    return _load_schedule()


class ScheduleItemUpdate(BaseModel):
    enabled: bool | None = None
    interval_hours: int | None = None


@router.post("/schedule/{task_key}")
def update_schedule(task_key: str, body: ScheduleItemUpdate):
    if task_key not in _SCHEDULE_TASKS:
        raise HTTPException(status_code=400, detail=f"不支持的任务: {task_key}")
    schedule = _load_schedule()
    if task_key not in schedule:
        schedule[task_key] = {"enabled": False, "interval_hours": 24, "last_run": None}
    if body.enabled is not None:
        schedule[task_key]["enabled"] = body.enabled
    if body.interval_hours is not None:
        schedule[task_key]["interval_hours"] = max(1, body.interval_hours)
    _save_schedule(schedule)
    return {"success": True, "message": f"定时任务 {task_key} 已更新", "schedule": schedule[task_key]}


@router.post("/schedule/{task_key}/run-now")
def run_scheduled_task_now(task_key: str):
    """立即执行一次定时任务（不影响下次定时执行时间）。"""
    if task_key not in _SCHEDULE_TASKS:
        raise HTTPException(status_code=400, detail=f"不支持的任务: {task_key}")
    _check_not_running()
    with _script_lock:
        _check_not_running()
    task_label, task_fn = _SCHEDULE_TASKS[task_key]
    _run_task(task_label, task_fn)
    return {"success": True, "message": f"{task_label} 已启动"}


# ============================================================
# rclone 检测
# ============================================================

@router.get("/rclone-info")
def rclone_info():
    installed = shutil.which("rclone") is not None
    remotes = []
    if installed:
        result = subprocess.run(["rclone", "listremotes"], capture_output=True, text=True, timeout=5, env=_rclone_env())
        if result.returncode == 0:
            remotes = [line.strip().rstrip(":") for line in result.stdout.split("\n") if line.strip()]
    return {
        "installed": installed,
        "remotes": remotes,
        "gdrive_configured": "gdrive" in remotes,
        "mega_configured": "mega" in remotes,
    }
