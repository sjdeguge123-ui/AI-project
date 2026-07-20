"""日志管理：每个会话生成以时间戳命名的 .log，自动管理总容量与单文件大小。

策略（用户 2026-07-20 调整）：
- 单个日志文件最大 2MB；
- 本次运行优先「追加」到当天最新且未满 2MB 的日志文件，避免生成一堆零散小文件；
  只有当天没有未满 2MB 的文件、或文件已写满 2MB 时，才新建带本次时间戳的文件；
- 单文件写满 2MB 时，自动切到同基础名的下一个序号文件（_1 -> _2 -> _3）；
- 总容量超 40MB 时，每次启动清理最旧文件；
- 日志目录已加入 .gitignore，不会上传远程；
- 每行日志前带 [YYYY-MM-DD HH:MM:SS] 时间戳，便于排查。
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path


# 日志配置：单文件 2MB，总容量 40MB
MAX_LOG_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 40 * 1024 * 1024


def setup_logging(project_root: Path) -> Path:
    """为本次运行设置日志：stdout 同时 tee 到日志文件，返回首个日志路径。

    - 日志目录：{project_root}/logs
    - 文件名格式：vidgrab_YYYYMMDD_HHMMSS_1.log
    - 追加策略：若当天已存在「未满 2MB」的日志文件，则直接追加进去（复用其文件名）；
      否则新建一个带本次时间戳的文件（_1 起）。
    - 单文件超过 2MB 时，自动切换到同一基础名的下一个后缀文件：_1 -> _2 -> _3
    - 总容量超过 40MB 时，每次启动清理最旧文件
    - 日志目录已加入 .gitignore，不会上传远程
    - 每行日志前带 [YYYY-MM-DD HH:MM:SS] 时间戳
    """
    logs_dir = project_root / "logs"
    logs_dir.mkdir(exist_ok=True)
    cleanup_logs(logs_dir, max_total_bytes=MAX_TOTAL_BYTES)

    today = datetime.now().strftime("%Y%m%d")

    # 找当天最新且未满 2MB 的日志文件，追加进去（减少零散小文件）
    chosen = None
    for p in sorted(
        [
            x for x in logs_dir.iterdir()
            if x.is_file() and x.suffix == ".log" and x.name.startswith(f"vidgrab_{today}_")
        ],
        key=lambda x: x.stat().st_mtime,
        reverse=True,
    ):
        if p.stat().st_size < MAX_LOG_BYTES:
            chosen = p
            break

    if chosen is None:
        # 当天没有可追加的文件：新建一个带本次时间戳的文件
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"vidgrab_{timestamp}"
        seq = 1
        log_path = logs_dir / f"{base_name}_{seq}.log"
        mode_desc = "新建"
    else:
        # 追加到现有文件
        log_path = chosen
        base_name = "_".join(chosen.stem.split("_")[:-1])
        seq = int(chosen.stem.split("_")[-1])
        mode_desc = "追加"

    sys.stdout = _Tee(sys.stdout, log_path, base_name=base_name)
    # 同时把 stderr 也 tee 进日志：未捕获异常（包括 Ctrl+C / BaseException 的 traceback）
    # 默认走 stderr 而非 stdout，若不接管就会「终端能看到报错、日志里没有」。
    sys.stderr = _Tee(sys.stderr, log_path, base_name=base_name)
    print(f"[日志] 本次日志{mode_desc}写入：{log_path}（stdout+stderr 均已记录；单文件<2M 时追加，满 2M 自动切分）")
    return log_path


def cleanup_logs(logs_dir: Path, max_total_bytes: int = MAX_TOTAL_BYTES) -> None:
    """删除最旧日志直到总容量低于 max_total_bytes。"""
    logs = sorted(
        [p for p in logs_dir.iterdir() if p.is_file() and p.suffix == ".log"],
        key=lambda p: p.stat().st_mtime,
    )
    total = sum(p.stat().st_size for p in logs)
    while total > max_total_bytes and logs:
        oldest = logs.pop(0)
        try:
            size = oldest.stat().st_size
            oldest.unlink()
            total -= size
        except OSError:
            break


class _Tee:
    """把 stdout 同时输出到控制台和日志文件，单文件超过 2M 自动切换同基础名的下一个序号文件。"""

    def __init__(self, stdout, log_path: Path, base_name: str, max_bytes: int = MAX_LOG_BYTES):
        self.stdout = stdout
        self.logs_dir = log_path.parent
        self.base_name = base_name
        self.max_bytes = max_bytes
        self._seq = int(log_path.stem.split("_")[-1])
        self.log_path = log_path
        self._file = open(log_path, "a", encoding="utf-8")
        self._written = self._file.tell()
        self._buffer = ""

    def write(self, data: str) -> None:
        try:
            self.stdout.write(data)
        except UnicodeEncodeError:
            # 终端编码无法显示某些字符时，用 replace 策略回退，避免进程崩溃
            self.stdout.write(data.encode("utf-8", "replace").decode("utf-8", "replace"))
        # 按行缓冲，确保每行都有时间戳前缀
        self._buffer += data
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._write_line(line)
        # 如果收到 flush/空行，也处理残留

    def flush(self) -> None:
        if self._buffer:
            self._write_line(self._buffer)
            self._buffer = ""
        self.stdout.flush()
        self._file.flush()

    def isatty(self) -> bool:
        return self.stdout.isatty()

    def _write_line(self, line: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        formatted = f"[{ts}] {line}\n"
        self._file.write(formatted)
        self._file.flush()
        self._written += len(formatted.encode("utf-8"))
        if self._written >= self.max_bytes:
            self._rotate()

    def _rotate(self) -> None:
        self._file.close()
        self._seq += 1
        self.log_path = self.logs_dir / f"{self.base_name}_{self._seq}.log"
        self._file = open(self.log_path, "a", encoding="utf-8")
        self._written = self._file.tell()

    def __getattr__(self, name: str):
        return getattr(self.stdout, name)
