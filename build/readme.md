# RTL、仿真与硬件构建

| 目录 | 内容 |
|---|---|
| `rtl/` | SPI采集、共享时基、TACH、FIFO、AXI、1×累加器、CORDIC及定向testbench |
| `overlay/` | 两套 overlay 的 Vivado Tcl、PS7 配置生成脚本及采集 XDC |
| `reports/historical_20261015/` | 原实测版本的资源、时序和DRC报告 |
| `reports/rebuild_20261102/` | 2026-11-02干净复建的资源、时序和DRC报告 |

## 构建两套overlay

使用Vivado 2026.1和PYNQ-Z2（xc7z020clg400-1）。从纯英文路径下的仓库根目录执行，输出目录必须是新的：

```text
python -B tools/prepare_board_files.py --out work/pynq_board_files
python -B build/overlay/gen_ps7_tcl.py work/pynq_board_files/pynq-z2/A.0/preset.xml build/overlay/ps7_cfg.tcl
vivado -mode batch -source build/overlay/acq_sync.tcl -tclargs C:/AMD/hx_capture_build work/pynq_board_files
vivado -mode batch -source build/overlay/order1x.tcl -tclargs C:/AMD/hx_compute_build work/pynq_board_files
```

Linux将输出位置替换为新的英文绝对路径。板卡依赖由工具从固定来源获取并验证SHA。生成物写入指定输出目录；历史实测镜像身份见[封存清单](../sealed_manifest.json)。新构建产物需单独核对板端行为。

## 定向仿真

`rtl/run_acq_sync_sim.tcl`、`rtl/run_order1x_axi_sim.tcl`、`rtl/run_cordic_sim.tcl`分别覆盖同步采集、计算AXI接口和CORDIC。使用Vivado batch模式运行相应入口；部分历史仿真脚本使用`C:/AMD`输出路径，运行前检查脚本中的工作目录。

[完整接线与构建说明](../docs/hardware.md) · [复建报告](reports/rebuild_20261102/readme.md) · [源码身份](../verification/hardware_rebuild_20261102.json)
