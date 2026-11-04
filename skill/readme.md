# PYNQ证据工作流技能

[技能源码](pynq_evidence_workflow/skill.md)说明采集保全、实际输入绑定、独立核对、派生字段一致性、overlay生命周期与分段性能。

源码交付路径统一小写。Agent Skills运行时要求SKILL.md，按[OpenAI技能规范](https://developers.openai.com/plugins/concepts/skills)在本地导出：

```text
python -B tools/export_skill.py --out work/pynq_evidence_workflow
```

输出work/pynq_evidence_workflow/SKILL.md可复制到支持Agent Skills的工具中；已有输出拒绝覆盖。该目录不进入Git跟踪，原始小写源码不变。本命令只完成格式导出，不声称已在另一块板卡验证。

先读技能说明并填写目标工程的时钟、ABI、单位、输入数量、参考和误差约定。测量/配重的物理前提不由技能流程认证。历史338项与32窗是技能形成时的证据，当前工程回归另行记录，不累计计数。
