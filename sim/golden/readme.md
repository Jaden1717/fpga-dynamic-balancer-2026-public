# Python参考与测量流程

从仓库根目录运行命令。PC离线分析依赖[固定Python环境](../../requirements-dev.txt)；硬件操作使用板端PYNQ环境。

| 模块 | 职责 |
|---|---|
| `measurement_run.py` | capture、review、compare统一入口 |
| `measurement_capture.py` | 镜像与配置检查、采集和运行条件记录 |
| `prepare_tach_order1x.py` · `angle_antialias.py` | 共享时基输入、角域重采样与抗混叠 |
| `order1x_fixed.py` · `verify_order1x.py` | 定点整数参考与FPGA结果核对 |
| `repeatability.py` | 固定窗口准备和统计 |
| `phase_free_balance.py` · `phase_model_review.py` | 幅值法模型与适用性诊断 |
| `sync_csv.py` · `timebase.py` | 原始文件与整数时间戳解释 |
| `measurement_review.py` | 时间域拟合与辅助诊断 |

`rotor_model.py`、`balance.py`、`sync_order.py`和`run_demo.py`保留早期合成模型，用于算法测试。合成结果与真实转子数据分别解释。

[快速开始](../../docs/quickstart.md) · [板端操作](../../docs/measurement.md) · [数值语义](../../docs/architecture.md)
