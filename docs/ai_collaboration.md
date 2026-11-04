# AI协作与纠错记录

本项目使用大模型辅助接口实现、测试设计、结果核对和文档整理。现场接线与实验操作由操作者完成，模型判断由实际输入、测试或明确的现场事实约束。

[原文节选](ai_excerpts.json)保存角色、内容、日期、片段SHA和原导出文件SHA。仅包含去身份的必要可见消息，不公开完整聊天或内部指令。下表为索引，连续案例见[技术报告第12章](../report/design_report.md#12-大模型协作与实际纠错)。

| 案例 | 提示与问题 | 模型回答及纠正 | 验证对应 |
|---|---|---|---|
| A01 结果校验与干净复现 | 审查测试遗漏 | 反例暴露浮点显示值漏检；干净目录发现缺少输入；补齐后重新验证 | [结果一致性用例](../tests/test_result_consistency.py) |
| A02 保持主算法 | 保留主方法，仅增加诊断 | 明确不替换主求解器，核对核心源码身份 | [阶段验证](../data/review/hx_measurement_flow_20261031/validation.json) |
| A03 实验状态纠正 | 操作者确认三次都保留C位置胶带 | 纠正“无试重参考”的误解，按真实状态解释数据 | [现场声明](../data/review/hx_fixed_c_reference_20261101/operator_declaration.json) |
| A04 因果解释修正 | 操作者澄清供电端操作方式 | 排除未经证实的装置端插拔前提，保留根因不确定性 | [供电补充](../data/review/hx_control_result_20261101/power_operation_followup.json) |

历史回复中的测试数量描述各自时间点，不与当前回归累加。上述经验提炼为[PYNQ证据工作流](../skill/pynq_evidence_workflow/skill.md)，包含输入绑定、独立核对、失败保全与overlay生命周期管理。
