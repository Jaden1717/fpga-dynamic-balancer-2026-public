# 板端运行

统一入口为[measure.sh](measure.sh)，调用PYNQ环境中的Python。完整的配置、采集与计算示例见[测量操作](../docs/measurement.md)。

| 文件 | 用途 |
|---|---|
| `measure.sh` | capture、review、compare命令入口 |
| `bringup/order1x_accel.py` | HX11计算寄存器驱动 |
| `bringup/sync_capture.py` | 同步采集接口实现与测试支持 |
| `bringup/adxl355_pl_acq.py` | ADXL355采集接口 |

历史实测使用[封存采集驱动](../sealed/capture_peer)、经 SHA256 核对的本地 XSA 及[停止观察器](../sealed/stop_evidence/hxs1_stop_snapshot.py)。请勿用同名开发驱动替换封存peer。

`bringup/`中其它探测、GPIO和旧采集脚本用于底层诊断，不能直接作为当前双overlay测量流程的替代入口。镜像和寄存器ABI须匹配。

[硬件连接](../docs/hardware.md) · [封存文件SHA](../sealed_manifest.json)
