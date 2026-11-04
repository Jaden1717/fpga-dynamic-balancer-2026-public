# 硬件与上板

平台：PYNQ-Z2 / xc7z020clg400-1、ADXL355/EVAL-PMDZ、NF-P12 redux-1700。风扇原生TACH为开集电极，4.7k上拉至3.3V；风扇独立约12V并共地，软件未控制供电。

| 信号 | Pmod | FPGA脚 |
|---|---|---|
| ADXL355 CS | JA1 | Y18 |
| MOSI | JA2 | Y19 |
| MISO | JA3 | Y16 |
| SCLK | JA4 | Y17 |
| DRDY | JA10 | W19 |
| TACH | JB1 | W14 |

历史读回DEVID=0xAD、FILTER=0x00、RANGE=0x81、POWER_CTL=0x00；这是现有工况配置，不直接推广为任意转子协议。

## 匹配版本

| 用途 | 文件 |
|---|---|
| 采集镜像 | `acq_sync.xsa`，按封存 SHA 核对手头的匹配镜像 |
| 采集peer | [adxl355_pl_acq.py](../sealed/capture_peer/adxl355_pl_acq.py)、[sync_capture.py](../sealed/capture_peer/sync_capture.py) |
| 停止旁证 | [hxs1_stop_snapshot.py](../sealed/stop_evidence/hxs1_stop_snapshot.py) |
| 计算镜像 | `order1x.xsa`，按封存 SHA 核对手头的匹配镜像 |
| 计算驱动 | [order1x_accel.py](../board/bringup/order1x_accel.py) |

精确SHA见[sealed_manifest.json](../sealed_manifest.json)。该清单记录历史实测的匹配版本。公开仓库提供构建源码和已保存的输出供离线复核。

[board/measure.sh](../board/measure.sh)固定调用`/usr/local/share/pynq-venv/bin/python3`，解释器缺失则退出。capture使用封存peer、停止观察器和实际setup，默认先生成CPU摘要；真实FPGA计算需显式提供compute-xsa。

setup声明与操作详见[测量操作](measurement.md)。新安装建立新setup，所有输出写新目录。

## 构建

板卡定义作为原厂外部依赖获取；准备工具验证归档及四个成员SHA，不在当前Git树再分发图片/XML。来源见[第三方说明](../third_party_notices.md)。输出目录必须是新的。准备板卡依赖后，从其 preset.xml 本地生成 PS7 配置。生成脚本拒绝覆盖已有配置。

```text
python -B tools/prepare_board_files.py --out work/pynq_board_files
python -B build/overlay/gen_ps7_tcl.py work/pynq_board_files/pynq-z2/A.0/preset.xml build/overlay/ps7_cfg.tcl
vivado -mode batch -source build/overlay/acq_sync.tcl -tclargs C:/AMD/hx_capture_rebuild work/pynq_board_files
vivado -mode batch -source build/overlay/order1x.tcl -tclargs C:/AMD/hx_compute_rebuild work/pynq_board_files
```

硬件构建前先将仓库检出到纯英文路径，例如C:/hx/fpga-dynamic-balancer-2026，并在Vivado命令行环境或配置好vivado路径的终端中，从仓库根目录执行上述命令。默认从固定原厂URL下载；已有ZIP可用`--archive LOCAL_ZIP`导入，仍需匹配固定SHA。工具链为Vivado2026.1，板卡／器件应匹配；Windows示例使用英文绝对构建路径，Linux应替换为新的英文可写绝对路径。两套Tcl的主要构建成果写入指定输出目录，不回写源码目录或sealed镜像。

2026-11-02构建使用英文目录内的源码副本，逐一核对Tcl、RTL、PS7配置和XDC与本仓库的SHA；具体身份在下方构建记录。这样避免Vivado在含中文的本机路径生成IP中间文件时出现路径问题。原始源码无需为适应工具而改动。

新构建结果需核对资源、时序、ABI与板端行为；构建成功不等于新镜像已经完成上板。当前使用的七份封存文件仍以原SHA为准。[路径适配说明](path_migration.md)

## 2026-11-02构建记录

| 设计 | LUT | FF | BRAM tile | DSP | WNS ns | WHS ns | DRC警告检查 |
|---|---:|---:|---:|---:|---:|---:|---:|
| acq_sync | 2039 | 2419 | 27.5 | 0 | 0.941 | 0.044 | 42 |
| order1x | 2240 | 1465 | 0 | 2 | 0.718 | 0.061 | 6 |

两套设计均在100MHz下完成综合、实现、位流和XSA生成，TNS/THS均为0。输入Tcl、RTL、PS7配置及采集XDC已与该次构建源码逐字节核对。新的资源与时序结果不是软件优化前后对照，也不代表新镜像已上板。保留原sealed实测镜像及七文件SHA。

采集报告保留RAMB异步控制警告及外部输入/输出延迟检查，工具日志也保留PS预置只读参数和DDR DQS偏移警告；内部WNS/WHS通过不等于外部接口或DDR完成独立签核。未为减少警告而改动板卡预置、RTL或实物协议。完整原始报告与机器可读来源一并保留。

[原始报告](../build/reports/rebuild_20261102/readme.md) · [输入/输出身份](../verification/hardware_rebuild_20261102.json)
