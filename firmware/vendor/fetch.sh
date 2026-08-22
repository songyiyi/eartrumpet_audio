#!/usr/bin/env bash
# 拉取 STM32 HAL 与 CMSIS。
#
# 全部来自 ST 的官方 GitHub 仓库，**不需要注册 ST 账号**，也不需要安装
# STM32CubeIDE。共约 100 MB，只克隆最新一次提交（--depth 1）。
#
# 这些是第三方代码，不进本仓库版本控制（见 .gitignore）。
set -euo pipefail
cd "$(dirname "$0")"
for r in stm32h7xx_hal_driver cmsis_device_h7 cmsis_core; do
  if [ -d "$r" ]; then
    echo "[$r] 已存在，跳过"
  else
    echo "[$r] 克隆中…"
    git clone --depth 1 --quiet "https://github.com/STMicroelectronics/$r.git"
    echo "[$r] 完成"
  fi
done
echo "依赖就绪。接着执行：cd ../nucleo-h743 && make"
