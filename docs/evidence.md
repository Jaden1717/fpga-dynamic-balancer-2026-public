# 证据索引

核对顺序：文件／传输身份 → 同输入算术 → 物理量解释 → 应用目标。前一级通过不自动证明后一级成立。

| 结论 | 主要来源 | 复核方式 |
|---|---|---|
| 封存版本身份 | [sealed_manifest.json](../sealed_manifest.json) | SHA256逐项比较 |
| 真实8窗输入及寄存器一致 | [独立核对](../data/review/hx_fpga_replay_20261101/independent_review.json) | [verify_eight_windows.py](../data/review/hx_fpga_replay_20261101/verify_eight_windows.py) |
| 固定C三次启停 | [独立结果](../data/review/hx_fixed_c_reference_20261101/independent_review.json)、[现场声明](../data/review/hx_fixed_c_reference_20261101/operator_declaration.json) | 原件、全窗及实际C状态分别核对 |
| 五状态与模型失败 | [独立结果](../data/review/hx_control_result_20261101/independent_review.json) | [批次重算](../tools/review_control_batch.py) |
| 最新B0—A—B1 | [原件](../data/physical/hx_20261102_001/board_export/reversible_01)、[独立结果](../data/review/hx_reversible_20261102/independent_review.json) | 3份完成、2份中断分别保留 |
| 软件回归 | [完整日志](../verification/publication_tests.txt) | 当前发布树的实际回归，命令见快速开始 |
| 历史资源／时序 | [routed报告](../build/reports/historical_20261015) | 历史Vivado2026.1，不称本次重建 |
| 当前源码干净重建 | [6份原始报告](../build/reports/rebuild_20261102/readme.md)、[源码与输出身份](../verification/hardware_rebuild_20261102.json) | 两套overlay完成实现/位流/XSA，内部时序闭合；新镜像未上板 |

data/physical保存原件、运行上下文和回传档案，data/review保存派生核对。重复归档维护接收身份，不算新增实验。

根目录[sha256sums.txt](../sha256sums.txt)对应当前发布文件。旧位置通过[迁移表](../verification/path_migration_20261102.json)定位；冻结载荷额外与原SHA核对。

raw不改字段、不统一换行、不为美观改路径；.gitattributes保留原字节。内部报名资料与完整聊天不上传，解释性案例按公开范围处理。
