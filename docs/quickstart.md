# 快速开始

离线复核不需要PYNQ或Vivado。命令在仓库根目录运行，建议使用较短、可写的路径。

## 环境

PC使用Python3.13，依赖固定在[requirements-dev.txt](../requirements-dev.txt)。板端沿用PYNQ虚拟环境，不直接套用PC依赖。

```bash
python -m venv .venv
```

PowerShell激活：`.venv\Scripts\Activate.ps1`。Linux/macOS激活：`source .venv/bin/activate`。也可直接调用虚拟环境内的Python。

```bash
python -m pip install -r requirements-dev.txt
python -B -m pytest -q -p no:cacheprovider --basetemp work/pytest-quickstart tests tools/controlled_trial/test_finish_control.py
```

套件包括工程回归、发布检查和依赖工具测试。basetemp专供测试，不存人工成果；指定工作区临时目录也可避免受限环境的默认临时目录权限问题。

## 复核已保存的FPGA执行

```bash
python -B data/review/hx_fpga_replay_20261101/verify_eight_windows.py
```

预期看到8行窗口结果及`8/8 verified`。它检查准备字、I/Q、幅相与原verification的一致性，不访问硬件。

## 从最新原件分析一份记录

```bash
python -B sim/golden/measurement_run.py review data/physical/hx_20261102_001/board_export/reversible_01/01_reference_before/raw --runtime data/physical/hx_20261102_001/board_export/reversible_01/01_reference_before/runtime.json --out work/review-b0-example
```

摘要应包含Z轴1×约0.539168mg、RPM约1714.26，来源是CPU reference。输出目录已有时换新名字，保留原目录。

## 复算五状态

```bash
python -B tools/review_control_batch.py work/control-review-example
```

复核5份原件、全部40窗和主模型诊断。模型闭合失败是实验的真实结果；命令成功不表示配重通过。

## 文件身份与常见问题

运行`python -B tools/verify_delivery.py`可跨平台检查受跟踪文件、全树SHA、维护文档链接及冻结载荷。Linux亦可运行`sha256sum -c sha256sums.txt`。清单不包含自身和个人新输出。

缺依赖时确认使用同一解释器；拒绝覆盖时更换输出路径；默认临时目录不可写时使用上述basetemp。封存驱动与上板环境见[硬件指南](hardware.md)。

## 路径与技能

当前路径统一小写；历史原件内的旧位置由[迁移表](path_migration.md)定位。技能导出：`python -B tools/export_skill.py --out work/pynq_evidence_workflow`。已有目录不会覆盖。
