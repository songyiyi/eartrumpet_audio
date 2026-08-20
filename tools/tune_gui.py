"""自声还原调参界面 —— 实时试听 + 频响曲线 + A/B 对比。

调参是这个项目真正的工作量所在：滤波器代码早已定型，接下来要反复做的只有
一件事 —— 找到让某个人觉得"像"的那组参数。而这本质上是"拖一下、听一下"的
循环，命令行把这个循环拉长到几十秒一次，效率很低。

音频处理走编译好的固件 C 核心（selfvoice.native），因此**试听到的就是固件
本身的代码**，逐位一致；Python 参考实现是逐样本循环，只有约 1.39x 实时，
无法安全驱动音频回调。

用法::

    python tools/build_native.py      # 先编译动态库（只需一次）
    python tools/tune_gui.py [音频文件.wav]

输入需为立体声：左声道 = 骨导，右声道 = 气导。
"""

from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selfvoice import read_wav, write_wav  # noqa: E402
from selfvoice.biquad import BiquadCascade, cascade_response_db  # noqa: E402
from selfvoice.console import setup as _console_setup  # noqa: E402
from selfvoice import native  # noqa: E402

BLOCK = 512  # 试听用的块长；与固件的 32 无关，这里只求回放稳定


class Player:
    """后台音频回放 —— 循环播放并实时处理。"""

    def __init__(self, chain: native.NativeChain, fs: float) -> None:
        self.chain = chain
        self.fs = fs
        self.lock = threading.Lock()
        self.stream = None
        self.pos = 0
        self.bone = np.zeros(0, dtype=np.float32)
        self.air = np.zeros(0, dtype=np.float32)
        self.bypass = False
        self.output_gain = 1.0
        self.peak = 0.0
        self.reduction_db = 0.0

    def set_audio(self, bone: np.ndarray, air: np.ndarray) -> None:
        with self.lock:
            self.bone = bone
            self.air = air
            self.pos = 0

    def _callback(self, outdata, frames, time_info, status):  # noqa: ARG002
        with self.lock:
            n = self.bone.shape[0]
            if n == 0:
                outdata[:] = 0.0
                return

            idx = (np.arange(frames) + self.pos) % n
            self.pos = (self.pos + frames) % n
            b = self.bone[idx]
            a = self.air[idx]

            if self.bypass:
                # 旁通 = 未经处理的气导信号，也就是普通麦克风录到的、
                # 别人听到的你。施加同样的输出增益，保证 A/B 电平可比。
                y = a * np.float32(self.output_gain)
                self.reduction_db = 0.0
            else:
                y = self.chain.process(b, a)
                self.reduction_db = self.chain.reduction_db

            self.peak = float(np.max(np.abs(y))) if y.size else 0.0
            outdata[:, 0] = np.clip(y, -1.0, 1.0)

    def start(self) -> None:
        import sounddevice as sd

        if self.stream is not None:
            return
        self.stream = sd.OutputStream(
            samplerate=self.fs, channels=1, dtype="float32",
            blocksize=BLOCK, callback=self._callback,
        )
        self.stream.start()

    def stop(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None


class TunerApp:
    def __init__(self, root: tk.Tk, wav_path: Path | None) -> None:
        self.root = root
        root.title("自声还原 · 调参")

        self.fs = 48000.0
        self.chain = native.NativeChain(self.fs)
        self.player = Player(self.chain, self.fs)
        self.path: Path | None = None
        self._plot_job = None

        self._build_ui()

        if wav_path and wav_path.exists():
            self.load(wav_path)
        self._apply_params()
        self._refresh_plot()
        self._tick()

    # ---------------------------------------------------------------- UI

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=10)
        main.grid(sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        # --- 文件与播放 ---
        top = ttk.Frame(main)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        ttk.Button(top, text="打开 WAV…", command=self._choose).pack(side="left")
        self.file_lbl = ttk.Label(top, text="未加载  (需立体声：左=骨导 右=气导)")
        self.file_lbl.pack(side="left", padx=10)

        self.play_btn = ttk.Button(top, text="▶ 播放", command=self._toggle_play,
                                   state="disabled")
        self.play_btn.pack(side="right")

        # --- 左侧：滑块 ---
        left = ttk.LabelFrame(main, text="面板旋钮", padding=10)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 10))

        self.mix = tk.DoubleVar(value=0.5)
        self.tone = tk.DoubleVar(value=0.5)
        self.out = tk.DoubleVar(value=0.75)

        self.mix_lbl = self._slider(left, 0, "混合比", self.mix,
                                    "0 = 纯气导   1 = 纯骨导")
        self.tone_lbl = self._slider(left, 1, "音色 / 厚度", self.tone,
                                     "0 = 薄亮   1 = 厚暗")
        self.out_lbl = self._slider(left, 2, "输出电平", self.out,
                                    "0.75 ≈ 0 dB")

        # --- 选项 ---
        opts = ttk.LabelFrame(left, text="选项", padding=8)
        opts.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(12, 0))

        self.bypass_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="A/B 旁通（听纯气导 = 别人听到的你）",
                        variable=self.bypass_var,
                        command=self._apply_params).grid(row=0, column=0,
                                                         sticky="w")

        self.delay_var = tk.StringVar(value="natural")
        drow = ttk.Frame(opts)
        drow.grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(drow, text="延迟模式:").pack(side="left")
        for txt, val in (("natural (423 µs)", "natural"),
                         ("coherent (73 µs)", "coherent")):
            ttk.Radiobutton(drow, text=txt, value=val, variable=self.delay_var,
                            command=self._apply_params).pack(side="left",
                                                             padx=(6, 0))

        self.lim_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="限幅器", variable=self.lim_var,
                        command=self._apply_params).grid(row=2, column=0,
                                                         sticky="w", pady=(6, 0))

        self.prox_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="近讲效应补偿（仅定向咪头需要）",
                        variable=self.prox_var,
                        command=self._apply_params).grid(row=3, column=0,
                                                         sticky="w")

        # --- 读数 ---
        info = ttk.LabelFrame(left, text="当前参数", padding=8)
        info.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        self.info_lbl = ttk.Label(info, text="", justify="left",
                                  font=("Consolas", 9))
        self.info_lbl.pack(anchor="w")

        # --- 导出 ---
        exp = ttk.Frame(left)
        exp.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        ttk.Button(exp, text="导出处理后的 WAV",
                   command=self._export_wav).pack(side="left")
        ttk.Button(exp, text="导出 C 系数",
                   command=self._export_c).pack(side="left", padx=(6, 0))

        # --- 右侧：频响 ---
        right = ttk.LabelFrame(main, text="频响", padding=6)
        right.grid(row=1, column=1, sticky="nsew")
        main.columnconfigure(1, weight=1)
        main.rowconfigure(1, weight=1)

        import matplotlib
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure

        # matplotlib 自带字体没有中文字形，不指定的话坐标轴标签会显示成方块。
        # 按可用性依次回退；unicode_minus 关掉是因为部分中文字体缺少 U+2212，
        # 负号同样会变方块。
        matplotlib.rcParams["font.sans-serif"] = [
            "Microsoft YaHei", "SimHei", "Noto Sans CJK SC",
            "PingFang SC", "DejaVu Sans",
        ]
        matplotlib.rcParams["axes.unicode_minus"] = False

        self.fig = Figure(figsize=(5.2, 4.0), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=right)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    def _slider(self, parent, row: int, name: str, var: tk.DoubleVar,
                hint: str) -> ttk.Label:
        ttk.Label(parent, text=name).grid(row=row, column=0, sticky="w",
                                          pady=(6, 0))
        val = ttk.Label(parent, text="0.50", width=6, font=("Consolas", 9))
        val.grid(row=row, column=2, sticky="e", pady=(6, 0))
        s = ttk.Scale(parent, from_=0.0, to=1.0, variable=var,
                      orient="horizontal", length=260,
                      command=lambda _v: self._apply_params())
        s.grid(row=row, column=1, sticky="ew", padx=8, pady=(6, 0))
        ttk.Label(parent, text=hint, foreground="#777",
                  font=("", 8)).grid(row=row, column=1, sticky="w", padx=8)
        parent.columnconfigure(1, weight=1)
        return val

    # ------------------------------------------------------------- 行为

    def _choose(self) -> None:
        p = filedialog.askopenfilename(
            title="选择双通道录音（左=骨导，右=气导）",
            filetypes=[("WAV 文件", "*.wav"), ("全部", "*.*")])
        if p:
            self.load(Path(p))

    def load(self, path: Path) -> None:
        try:
            samples, fs = read_wav(path)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("读取失败", str(e))
            return
        if samples.shape[1] < 2:
            messagebox.showerror(
                "需要立体声",
                f"输入必须是立体声（左=骨导，右=气导），实际为 "
                f"{samples.shape[1]} 声道。")
            return

        self.path = path
        was_playing = self.player.stream is not None
        self.player.stop()

        if float(fs) != self.fs:
            self.fs = float(fs)
            self.chain.close()
            self.chain = native.NativeChain(self.fs)
            self.player = Player(self.chain, self.fs)

        self.player.set_audio(np.ascontiguousarray(samples[:, 0]),
                              np.ascontiguousarray(samples[:, 1]))
        dur = samples.shape[0] / fs
        self.file_lbl.config(text=f"{path.name}   {fs:.0f} Hz   {dur:.2f} s")
        self.play_btn.config(state="normal")
        self._apply_params()
        self._refresh_plot()
        if was_playing:
            self._toggle_play()

    def _toggle_play(self) -> None:
        if self.player.stream is None:
            try:
                self.player.start()
            except Exception as e:  # noqa: BLE001
                messagebox.showerror("无法打开音频设备", str(e))
                return
            self.play_btn.config(text="■ 停止")
        else:
            self.player.stop()
            self.play_btn.config(text="▶ 播放")

    def _apply_params(self) -> None:
        mix, tone, out = self.mix.get(), self.tone.get(), self.out.get()
        self.mix_lbl.config(text=f"{mix:.2f}")
        self.tone_lbl.config(text=f"{tone:.2f}")
        self.out_lbl.config(text=f"{out:.2f}")

        with self.player.lock:
            self.chain.set_knobs(mix, tone, out)
            self.chain.set_options(self.delay_var.get(), self.lim_var.get(),
                                   self.prox_var.get())
            self.player.bypass = self.bypass_var.get()
            self.player.output_gain = 10.0 ** (
                self.chain.info(native.INFO_OUTPUT_GAIN_DB) / 20.0)

        # 拖动滑块时合并重绘请求，避免每一个像素都触发一次绘图
        if self._plot_job is not None:
            self.root.after_cancel(self._plot_job)
        self._plot_job = self.root.after(60, self._refresh_plot)

    def _refresh_plot(self) -> None:
        self._plot_job = None
        freqs = np.logspace(np.log10(20.0), np.log10(self.fs / 2.0), 400)

        bone_db = cascade_response_db(
            BiquadCascade(self.chain.coeffs("bone")), freqs, self.fs)
        air_db = cascade_response_db(
            BiquadCascade(self.chain.coeffs("air")), freqs, self.fs)

        ratio = self.chain.info(native.INFO_BONE_RATIO)
        bone_gain_db = self.chain.info(native.INFO_BONE_GAIN_DB)

        self.ax.clear()
        self.ax.semilogx(freqs, bone_db + bone_gain_db, color="#b0762b",
                         lw=1.8, label=f"骨导路 (占比 {ratio:.2f})")
        self.ax.semilogx(freqs, air_db, color="#1f6e80", lw=1.8,
                         label=f"气导路 (占比 {1 - ratio:.2f})")
        self.ax.axhline(0.0, color="#bbb", lw=0.8, ls=":")
        self.ax.set_xlim(20, self.fs / 2)
        self.ax.set_ylim(-40, 30)
        self.ax.set_xlabel("频率 (Hz)")
        self.ax.set_ylabel("增益 (dB)")
        self.ax.grid(True, which="both", alpha=0.25)
        self.ax.legend(loc="lower left", fontsize=8)
        self.fig.tight_layout()
        self.canvas.draw_idle()

    def _tick(self) -> None:
        """周期性刷新读数。"""
        peak = self.player.peak
        peak_db = 20 * np.log10(peak) if peak > 1e-9 else -99.0
        red = self.player.reduction_db

        lines = [
            f"骨导占比    {self.chain.info(native.INFO_BONE_RATIO):.2f}",
            f"骨导补偿EQ  300Hz {self.chain.info(native.INFO_EQ0_GAIN_DB):+.1f} dB"
            f"   1200Hz {self.chain.info(native.INFO_EQ1_GAIN_DB):+.1f} dB",
            f"骨导低通    {self.chain.info(native.INFO_BONE_LP_HZ):.0f} Hz",
            f"气导延迟    {int(self.chain.info(native.INFO_DELAY_SAMPLES))} 样本",
            f"输出增益    {self.chain.info(native.INFO_OUTPUT_GAIN_DB):+.1f} dB",
            f"输出峰值    {peak_db:+.1f} dBFS"
            + (f"   限幅 {red:.1f} dB" if red < -0.1 else ""),
        ]
        self.info_lbl.config(text="\n".join(lines))
        self.root.after(120, self._tick)

    # ------------------------------------------------------------- 导出

    def _export_wav(self) -> None:
        if self.path is None:
            messagebox.showinfo("先加载音频", "请先打开一个双通道 WAV 文件。")
            return
        out = filedialog.asksaveasfilename(
            defaultextension=".wav", filetypes=[("WAV 文件", "*.wav")],
            initialfile=f"{self.path.stem}_tuned.wav")
        if not out:
            return
        with self.player.lock:
            self.chain.reset()
            y = self.chain.process(self.player.bone, self.player.air)
            self.chain.reset()
        write_wav(out, y, int(self.fs), bits=24)
        messagebox.showinfo("已导出", f"{out}\n峰值 "
                            f"{20 * np.log10(max(float(np.max(np.abs(y))), 1e-9)):+.1f} dBFS")

    def _export_c(self) -> None:
        out = filedialog.asksaveasfilename(
            defaultextension=".h", filetypes=[("C 头文件", "*.h")],
            initialfile="sv_coeffs.h")
        if not out:
            return
        from selfvoice import ChainParams, SelfVoiceChain
        from selfvoice.export import export_c_header
        from selfvoice.params import (apply_mix_knob, apply_output_knob,
                                      apply_tone_knob)

        p = ChainParams(fs=self.fs)
        apply_mix_knob(p, self.mix.get())
        apply_tone_knob(p, self.tone.get())
        apply_output_knob(p, self.out.get())
        p.air.delay_mode = self.delay_var.get()
        p.limiter.enabled = self.lim_var.get()
        p.air.proximity_comp_enabled = self.prox_var.get()
        export_c_header(SelfVoiceChain(p), out)
        messagebox.showinfo("已导出", out)

    def on_close(self) -> None:
        self.player.stop()
        self.chain.close()
        self.root.destroy()


def main() -> int:
    _console_setup()

    if not native.is_available():
        print("错误：找不到原生动态库。", file=sys.stderr)
        print("请先运行：python tools/build_native.py", file=sys.stderr)
        return 1

    default = Path("out/testsig.wav")
    wav = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        default if default.exists() else None)

    root = tk.Tk()
    app = TunerApp(root, wav)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
