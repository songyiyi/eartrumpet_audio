"""从 Nucleo 采集固件接收双通道音频，存成 WAV。

配合 firmware/nucleo-h743 使用。固件把 DFSDM 解码后的两路 24 位样本经
ST-Link 虚拟串口送出，本脚本负责组帧、校验、存盘，并给出诊断读数。

诊断刻意放在电脑侧算而不是固件里：样本数据本来就要送过来，让固件再算
一遍是重复劳动；而且文本诊断和二进制流共用一个串口会互相破坏。

用法::

    python tools/capture.py --list                     列出串口
    python tools/capture.py COM5 out/rec.wav -d 20     录 20 秒
    python tools/capture.py COM5 --monitor             只看诊断，不存盘

`--monitor` 是**敲击测试**用的：敲一下骨导传感器，看哪个通道的 RMS 跳，
据此确定左右声道对应哪颗传感器。不要靠推理判断。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selfvoice.console import setup as _console_setup  # noqa: E402
from selfvoice.wavio import write_wav  # noqa: E402

MAGIC = b"SV"
BLOCK_SAMPLES = 256          # 必须与固件 board.h 的 BOARD_BLOCK_SAMPLES 一致
FRAME_BYTES = 4 + BLOCK_SAMPLES * 6
SAMPLE_RATE = 48000
FULL_SCALE = 1 << 23         # 24 位有符号满量程


def list_ports() -> int:
    try:
        from serial.tools import list_ports
    except ImportError:
        print("需要 pyserial：pip install pyserial", file=sys.stderr)
        return 1
    ports = list(list_ports.comports())
    if not ports:
        print("未找到任何串口。检查 Nucleo 是否已插上 USB。")
        return 1
    print("可用串口：")
    for p in ports:
        tag = "  ← 很可能是这个" if "STLink" in (p.description or "") or \
              "ST-Link" in (p.description or "") else ""
        print(f"  {p.device:<10} {p.description}{tag}")
    return 0


def decode_frame(buf: memoryview) -> tuple[int, np.ndarray, np.ndarray]:
    """解析一帧，返回 (序号, 通道0, 通道1)，样本为 float32 归一化到 ±1。"""
    seq = buf[2] | (buf[3] << 8)
    raw = np.frombuffer(buf[4:], dtype=np.uint8).reshape(-1, 6).astype(np.int32)

    # 小端 24 位有符号
    a = raw[:, 0] | (raw[:, 1] << 8) | (raw[:, 2] << 16)
    b = raw[:, 3] | (raw[:, 4] << 8) | (raw[:, 5] << 16)
    a = np.where(a & 0x800000, a - 0x1000000, a)
    b = np.where(b & 0x800000, b - 0x1000000, b)

    return seq, (a / FULL_SCALE).astype(np.float32), (b / FULL_SCALE).astype(np.float32)


def db(x: float) -> float:
    return 20.0 * np.log10(x) if x > 1e-12 else -np.inf


def diagnose(ch0: np.ndarray, ch1: np.ndarray) -> str:
    """一行诊断读数。没有示波器时这就是主要观测手段。"""
    parts = []
    for name, x in (("CH0", ch0), ("CH1", ch1)):
        rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))
        pk = float(np.max(np.abs(x))) if x.size else 0.0
        dc = float(np.mean(x))
        flag = ""
        if abs(dc) > 0.5:
            flag = " ⚠数据线可能卡死"
        elif rms < 1e-6:
            flag = " ⚠无信号"
        parts.append(f"{name} RMS {db(rms):+6.1f} 峰值 {db(pk):+6.1f} "
                     f"DC {dc:+.4f}{flag}")
    return "   ".join(parts)


def main() -> int:
    _console_setup()
    ap = argparse.ArgumentParser(description="接收 Nucleo 采集固件的双通道音频")
    ap.add_argument("port", nargs="?", help="串口，如 COM5 或 /dev/ttyACM0")
    ap.add_argument("output", nargs="?", type=Path, help="输出 WAV")
    ap.add_argument("--list", action="store_true", help="列出可用串口")
    ap.add_argument("--baud", type=int, default=4000000, help="波特率，默认 4M")
    ap.add_argument("-d", "--duration", type=float, default=20.0, help="录制秒数")
    ap.add_argument("--monitor", action="store_true",
                    help="只显示诊断不存盘（敲击测试用）")
    args = ap.parse_args()

    if args.list:
        return list_ports()
    if not args.port:
        ap.error("需要指定串口，或用 --list 查看")
    if not args.monitor and not args.output:
        ap.error("需要指定输出文件，或用 --monitor")

    try:
        import serial
    except ImportError:
        print("需要 pyserial：pip install pyserial", file=sys.stderr)
        return 1

    try:
        ser = serial.Serial(args.port, args.baud, timeout=1)
    except Exception as e:  # noqa: BLE001
        print(f"无法打开 {args.port}: {e}", file=sys.stderr)
        return 1

    print(f"已连接 {args.port} @ {args.baud} baud")
    print(f"采样率 {SAMPLE_RATE} Hz，每帧 {BLOCK_SAMPLES} 样本")
    print("按 Ctrl+C 停止\n")

    ch0_all: list[np.ndarray] = []
    ch1_all: list[np.ndarray] = []
    buf = bytearray()
    last_seq: int | None = None
    dropped = 0
    frames = 0
    t0 = time.time()
    next_report = t0 + 1.0

    try:
        while True:
            chunk = ser.read(FRAME_BYTES * 4)
            if chunk:
                buf.extend(chunk)

            # 按 magic 组帧。串口起始时多半会落在帧中间，靠 magic 重新对齐。
            while len(buf) >= FRAME_BYTES:
                i = buf.find(MAGIC)
                if i < 0:
                    buf.clear()
                    break
                if i > 0:
                    del buf[:i]
                    continue
                if len(buf) < FRAME_BYTES:
                    break

                seq, a, b = decode_frame(memoryview(buf)[:FRAME_BYTES])
                del buf[:FRAME_BYTES]
                frames += 1

                # 序号跳变即固件丢过块（串口带宽不够）。
                # 固件板上红灯也会亮，两处互相印证。
                if last_seq is not None:
                    gap = (seq - last_seq - 1) & 0xFFFF
                    if gap:
                        dropped += gap
                last_seq = seq

                if not args.monitor:
                    ch0_all.append(a)
                    ch1_all.append(b)

            now = time.time()
            if now >= next_report:
                next_report = now + 1.0
                if frames:
                    line = diagnose(a, b)
                    drop_txt = f"   丢块 {dropped}" if dropped else ""
                    print(f"\r{now - t0:5.1f}s  {line}{drop_txt}", end="", flush=True)

            if not args.monitor and (now - t0) >= args.duration:
                break

    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        ser.close()

    print()
    if frames == 0:
        print("没有收到任何数据。检查：", file=sys.stderr)
        print("  1. 固件是否已烧录（板上绿灯应每秒闪一次）", file=sys.stderr)
        print("  2. 波特率是否与固件 BOARD_VCP_BAUD 一致", file=sys.stderr)
        print("  3. 串口号是否正确（--list 查看）", file=sys.stderr)
        return 1

    if dropped:
        print(f"警告：丢失 {dropped} 块。串口带宽不足，"
              f"可降低固件的 BOARD_VCP_BAUD 或改送 16 位。")

    if args.monitor:
        print(f"共收到 {frames} 帧。")
        return 0

    ch0 = np.concatenate(ch0_all)
    ch1 = np.concatenate(ch1_all)
    stereo = np.stack([ch0, ch1], axis=1)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_wav(args.output, stereo, SAMPLE_RATE, bits=24)

    print(f"已写出 {args.output}  {ch0.shape[0] / SAMPLE_RATE:.2f} s")
    print(f"  左声道 CH0  峰值 {db(float(np.max(np.abs(ch0)))):+.1f} dBFS")
    print(f"  右声道 CH1  峰值 {db(float(np.max(np.abs(ch1)))):+.1f} dBFS")
    print()
    print("下一步：python tools/tune_gui.py", args.output)
    print("注意：确认左声道是骨导。若相反，用 --monitor 做敲击测试核对。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
