"""GPU 稳定性实测工具（仅本地验证用，不进 git 主流程）。

目的：在真实长音频上，对多种 (CT2_CUDA_ALLOCATOR, compute_type, chunk_sec) 组合
逐一实测 GPU 转录是否稳定（**禁用 CPU 回退**，这样 GPU 一崩就直接失败，才能暴露真实稳定性）。

策略：按「最可能稳定」的顺序试，命中第一个全程成功的组合即停止（用户要求试遍直到解决）。
若所有组合都崩 → 判定 Windows 下该 GPU 路径不可靠，结论交给调用方决定（保留 CPU 兜底）。

用法：
  python tools/gpu_stress.py --wav <16k.wav> [--model base]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_one(wav: str, model: str, allocator: str, compute_type: str, chunk_sec: int) -> tuple:
    """用 core.transcribe_worker 以纯 GPU（device=cuda，无回退）跑一遍。

    返回 (ok: bool, detail: str)。
    """
    json_path = wav + ".stress.json"
    if os.path.exists(json_path):
        try:
            os.unlink(json_path)
        except Exception:
            pass

    env = os.environ.copy()
    env["CT2_CUDA_ALLOCATOR"] = allocator

    cmd = [
        sys.executable, "-u", "-m", "core.transcribe_worker",
        "--audio-path", wav,
        "--model-size", model,
        "--device", "cuda",          # 强制 GPU，不允许回退 → 真暴露 GPU 稳定性
        "--compute-type", compute_type,
        "--chunk-sec", str(chunk_sec),
        "--output-json", json_path,
    ]
    t0 = time.time()
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, encoding="utf-8", errors="replace",
        cwd=PROJECT_ROOT, env=env,
    )
    last = ""
    assert proc.stdout is not None
    for line in proc.stdout:
        s = line.rstrip("\n")
        last = s
        print(f"   [{allocator}/{compute_type}/{chunk_sec}s] {s}")
    rc = proc.wait()
    dt = time.time() - t0

    ok = rc == 0 and os.path.exists(json_path)
    if ok:
        try:
            import json
            with open(json_path, "r", encoding="utf-8") as f:
                n = len(json.load(f))
            detail = f"OK segments={n} 用时={dt:.0f}s"
        except Exception:
            detail = f"OK(但读 json 失败) 用时={dt:.0f}s"
        try:
            os.unlink(json_path)
        except Exception:
            pass
    else:
        detail = f"FAIL rc={rc} 用时={dt:.0f}s 末行={last[:120]}"
    return ok, detail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--model", default="base")
    args = ap.parse_args()

    # 按「最可能稳定」优先排序试：先换分配器（async 能显著缓解 CUDA 碎片化），
    # 再降 compute_type，最后减小块（降低每块峰值）。
    configs = [
        ("cuda_malloc_async", "int8_float16", 120),
        ("cuda_malloc_async", "int8", 120),
        ("cub_caching", "int8_float16", 120),
        ("cuda_malloc_async", "float16", 120),
        ("cuda_malloc_async", "int8_float16", 60),
        ("default", "int8_float16", 120),
    ]

    print("=" * 70)
    print(" GPU 稳定性实测（禁用 CPU 回退，纯 GPU 验证）")
    print(f" 音频：{args.wav}")
    print(f" 模型：{args.model}")
    print(f" 待测组合：{len(configs)} 个")
    print("=" * 70)

    winner = None
    for i, (alloc, ct, ck) in enumerate(configs, 1):
        print(f"\n--- 组合 {i}/{len(configs)}：allocator={alloc} compute_type={ct} chunk={ck}s ---")
        try:
            ok, detail = run_one(args.wav, args.model, alloc, ct, ck)
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"EXC {e}"
        print(f"结果：{'✅ 稳定' if ok else '❌ 不稳定'}  {detail}")
        if ok:
            winner = (alloc, ct, ck)
            print(f"\n🎉 命中稳定组合：CT2_CUDA_ALLOCATOR={alloc} compute_type={ct} chunk={ck}s")
            break
        else:
            print("   继续试下一个组合...")

    print("\n" + "=" * 70)
    if winner:
        print(f"结论：GPU 在该组合下稳定 -> {winner}")
        return 0
    print("结论：所有组合均不稳定 → Windows 下该 GPU 路径不可靠，建议保留 CPU 兜底（whisper.device: cpu）")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
