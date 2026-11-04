# 板端测量操作

## 环境和连接

使用PYNQ-Z2、ADXL355和原生2PPR TACH。按[硬件指南](hardware.md)完成3.3V信号连接和共地；风扇使用独立电源。板端入口固定使用`/usr/local/share/pynq-venv/bin/python3`，缺失时退出，不回退到另一套Python。

将仓库复制到板端可写的英文路径，在仓库根目录执行命令。先阅读[封存版本清单](../sealed_manifest.json)；实际采集使用其中匹配的 peer 和镜像。下面命令假定操作者已有与清单 SHA256 一致的历史镜像，并将其放入 `work/local_overlays/`。新构建镜像应另行验证 ABI、配置与驱动后再接入采集流程。

## 记录安装条件

创建`work/setup.json`，下例为无试重状态的格式示例。每个标识均须按实际安装填写，未知字段保留`null`，不能复制历史实验身份：

```json
{
  "fixture_id": "fixture_01",
  "sensor_mount_id": "sensor_01",
  "cable_relief": "fixed_to_fixture",
  "environment": "bench_01",
  "power_setting": "12v_supply_01",
  "trial_position": "none",
  "trial_mass_g": 0,
  "trial_radius_mm": null
}
```

字段定义以[measurement_run.py](../sim/golden/measurement_run.py)中的`SETUP_FIELDS`为准。运行条件声明与自动读取的启动身份、配置寄存器分别保存。

## 采集并分析

```bash
sudo bash board/measure.sh capture   --peer-directory sealed/capture_peer   --evidence-directory sealed/stop_evidence   --xsa work/local_overlays/acq_sync.xsa   --seconds 40 --setup work/setup.json --out work/capture_01
```

40秒与当前约1700RPM、Z轴、8×128圈协议匹配。先停源、排空并保存原件，再生成CPU参考摘要。输出目录必须是新的；异常或中断保留已取得的数据和运行状态。

## 在FPGA计算保存的记录

```bash
sudo bash board/measure.sh review work/capture_01/raw   --runtime work/capture_01/runtime.json   --compute-xsa work/local_overlays/order1x.xsa --out work/fpga_review_01
```

该命令切换到计算overlay，保存实际输入、寄存器结果和逐窗核对。切换后不要继续访问旧overlay的MMIO对象。不指定`--compute-xsa`时只执行CPU参考分析。

## 同状态比较

```bash
python sim/golden/measurement_run.py compare work/capture_01/summary.json work/capture_02/summary.json --out work/comparison.json
```

`UNKNOWN`表示缺少比较条件，`NOT_COMPARABLE`表示条件不匹配，`DECLARED_CONDITIONS_MATCH`仅表示记录与声明匹配。RPM容差用`--rpm-relative-tolerance`预先指定；没有容差时保持未知。同状态比较不用于把不同试重状态认定为相同状态。

## 输出说明

| 文件 | 内容 |
|---|---|
| `raw/` | 原始样本、转速事件、采集状态和停止证据 |
| `runtime.json` | 启动身份、初始化、配置与人工声明 |
| `summary.json` | Z轴1×峰值、SD、CV、RPM与结果来源 |
| `run.json` | 完成/失败状态与分段计时 |

保存和备份记录后按[数据索引](evidence.md)的方法复核。使用内存文件系统时，应在断电前导出并核对副本。
