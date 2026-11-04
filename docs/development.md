# 开发与验证

## 软件回归

使用Python 3.13，从仓库根目录执行：

```bash
python -m pip install -r requirements-dev.txt
python -B -m pytest -q -p no:cacheprovider --basetemp work/pytest-dev tests tools/controlled_trial/test_finish_control.py
python -B data/review/hx_fpga_replay_20261101/verify_eight_windows.py
python -B tools/review_control_batch.py work/control-review-dev
```

测试覆盖输入完整性、时间戳、角域准备、定点参考、MMIO、超时和失败保全、结果一致性。`tests/fixtures/rtl_capture/`包含固定RTL输入，干净检出无需额外复制本机数据。测试临时目录不能存放人工成果。

第二条复核保存的8窗FPGA输出；第三条重算五状态40窗，保留原主模型的失败结果。它们不访问开发板。

## 发布文件检查

```bash
python -B tools/verify_delivery.py
python -B tools/export_skill.py --out work/skill-export
```

检查受跟踪路径、当前SHA清单、维护文档链接和冻结证据字节。历史数据中的来源路径保持原样，使用[映射表](../verification/path_migration_20261102.json)定位。

## 持续集成

[GitHub Actions](../.github/workflows/validate.yml)在Linux和Windows执行以上离线验证。Vivado构建与板端运行是独立验证层，不由Python CI替代。

## 硬件改动

修改RTL、XDC或接口后运行相应定向仿真和完整实现，核对资源、时序、DRC及驱动ABI，并为新镜像建立独立板端记录。[构建入口](../build/readme.md) · [现有构建身份](../verification/hardware_rebuild_20261102.json)
