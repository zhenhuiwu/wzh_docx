"""服务运行标记：服务启动时对 <库名>.service.lock 持有共享锁，停止（或进程退出）时自动释放。

维护脚本（如 rollback_migration_114）用非阻塞独占锁探测：拿不到锁说明有服务正在使用这个库。
锁随进程退出由操作系统释放，进程崩溃不会留下“假运行”状态；文件本身留着无妨。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import IO

logger = logging.getLogger(__name__)

_held: IO[str] | None = None


def lock_path(database: Path) -> Path:
    return database.with_name(f"{database.name}.service.lock")


def _try_lock(handle: IO[str], exclusive: bool) -> bool:
    if os.name == "nt":  # pragma: no cover - Windows
        import msvcrt
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl
    try:
        fcntl.flock(handle.fileno(), (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(handle: IO[str]) -> None:
    try:
        if os.name == "nt":  # pragma: no cover - Windows
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def acquire(database: Path) -> None:
    """服务启动时调用。拿不到锁（例如同库已有另一个服务进程）只记日志，不影响启动。"""
    global _held
    if _held is not None:
        return
    try:
        database.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path(database), "a+", encoding="utf-8")
    except OSError as error:
        logger.warning("service lock unavailable: %s", error)
        return
    if _try_lock(handle, exclusive=False):
        _held = handle
    else:
        handle.close()
        logger.warning("service lock held by another process: %s", lock_path(database))


def release() -> None:
    global _held
    if _held is not None:
        _held.close()
        _held = None


def service_running(database: Path) -> bool:
    """有服务进程持有该库的运行标记时返回 True。"""
    path = lock_path(database)
    if not path.exists():
        return False
    try:
        handle = open(path, "a+", encoding="utf-8")
    except OSError:
        return True
    try:
        locked = _try_lock(handle, exclusive=True)
        if locked:
            _unlock(handle)  # Windows 建议先解锁再关闭，避免紧接着启动的服务短暂拿不到锁
        return not locked
    finally:
        handle.close()


def processes_with_file(database: Path) -> list[int]:
    """Linux：列出打开了该库（含 -wal/-shm）的其他进程（看不到的进程跳过）。其他系统返回空。"""
    proc = Path("/proc")
    if not proc.is_dir():
        return []
    targets = {str(database.resolve()), f"{database.resolve()}-wal", f"{database.resolve()}-shm"}
    found: list[int] = []
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            links = {os.readlink(fd) for fd in (entry / "fd").iterdir()}
        except OSError:
            continue
        if links & targets:
            found.append(int(entry.name))
    return sorted(found)
