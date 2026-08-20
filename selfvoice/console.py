"""Windows 控制台编码修正。

Windows 的默认控制台代码页（简中系统为 cp936）会把 UTF-8 中文输出显示成
乱码。所有命令行工具在入口处调用一次 setup() 即可，无需用户设置环境变量。
"""

from __future__ import annotations

import sys


def setup() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                # 被重定向到不支持重配置的目标时静默跳过
                pass
