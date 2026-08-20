"""把固件的 C 核心编译成主机动态库，供调参界面实时调用。

调参界面需要实时处理音频，而 Python 参考实现是逐样本循环，实测只有
1.39x 实时（约 80% CPU 占用），扛不住实时回放。编译同一份固件代码为
动态库后由 ctypes 调用，速度绰绰有余，而且**试听到的就是固件本身的
代码**，比另引入一套滤波器更贴近真机。

编译参数与对拍测试保持一致（尤其是 -ffp-contract=off），因此动态库的
输出与已验证的固件行为完全相同。

用法::

    python tools/build_native.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "firmware" / "core"
HOST = ROOT / "firmware" / "host"
OUT_DIR = ROOT / "selfvoice" / "_native"

SOURCES = [
    HOST / "sv_native.c",
    CORE / "sv_biquad.c",
    CORE / "sv_design.c",
    CORE / "sv_chain.c",
]

# 与 firmware/test/Makefile 保持一致：关闭浮点收缩，否则编译器会把
# a*b+c 融合成 FMA，动态库的行为将与已对拍验证的固件不再逐位相同。
CFLAGS = [
    "-std=c99",
    "-O2",
    "-Wall",
    "-Wextra",
    "-ffp-contract=off",
    "-fno-fast-math",
    "-fno-unsafe-math-optimizations",
    "-shared",
    "-fPIC",
]

# 常见的 MinGW 安装位置，PATH 里找不到时逐个尝试
FALLBACK_GCC = [
    Path.home() / ".local" / "mingw64" / "bin" / "gcc.exe",
    Path("C:/Program Files/mingw64/bin/gcc.exe"),
    Path("C:/mingw64/bin/gcc.exe"),
]


def find_compiler() -> str | None:
    found = shutil.which("gcc") or shutil.which("cc") or shutil.which("clang")
    if found:
        return found
    for cand in FALLBACK_GCC:
        if cand.exists():
            return str(cand)
    return None


def library_name() -> str:
    if sys.platform == "win32":
        return "sv_core.dll"
    if sys.platform == "darwin":
        return "libsv_core.dylib"
    return "libsv_core.so"


def main() -> int:
    cc = find_compiler()
    if cc is None:
        print("错误：找不到 C 编译器（gcc / cc / clang）。", file=sys.stderr)
        print("Windows 上可安装 MinGW-w64；若已安装，请确认其 bin 目录在 PATH 中。",
              file=sys.stderr)
        return 1

    missing = [str(s) for s in SOURCES if not s.exists()]
    if missing:
        print("错误：缺少源文件：", file=sys.stderr)
        for m in missing:
            print("   ", m, file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / library_name()

    cmd = [cc, *CFLAGS, "-o", str(target), *[str(s) for s in SOURCES], "-lm"]
    print("编译器:", cc)
    print("目标  :", target)
    print()

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.stdout.strip():
        print(proc.stdout)
    if proc.stderr.strip():
        print(proc.stderr, file=sys.stderr)

    if proc.returncode != 0:
        print(f"编译失败（退出码 {proc.returncode}）", file=sys.stderr)
        return proc.returncode

    size = target.stat().st_size
    print(f"编译成功：{target.name}  ({size:,} 字节)")
    print(f"Python {sysconfig.get_python_version()} 可通过 selfvoice.native 加载")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
