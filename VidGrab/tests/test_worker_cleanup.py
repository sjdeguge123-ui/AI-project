"""验证转录 worker 子进程的生命周期清理：主进程退出前能强制杀掉活跃 worker。

直接验证 core/transcriber 的 _kill_active_worker + _ACTIVE_WORKER 记录机制，
无需 faster-whisper 等重依赖，可在任意环境跑。
"""
import subprocess
import sys
import time

from core import transcriber


def test_kill_active_worker_terminates_child():
    # 启动一个会睡 30s 的假 worker，模拟转录子进程
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    transcriber._ACTIVE_WORKER = proc
    try:
        assert proc.poll() is None, "子进程应当还在运行"
        transcriber._kill_active_worker()
        # 给 kill 一点生效时间
        for _ in range(50):
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        assert proc.poll() is not None, "worker 应已被强制终止，不应遗留成孤儿"
    finally:
        # 兜底：测试异常时也确保不遗留子进程
        if proc.poll() is None:
            proc.kill()
        transcriber._ACTIVE_WORKER = None


def test_kill_active_worker_noop_when_none():
    transcriber._ACTIVE_WORKER = None
    transcriber._kill_active_worker()  # 不应抛异常
    assert transcriber._ACTIVE_WORKER is None
