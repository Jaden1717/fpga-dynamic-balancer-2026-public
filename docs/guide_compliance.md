# AMD自主选题指南对照

依据2026年AMD选题指南第3.3节（PDF第22—25页）。原文SHA256：`c6ad82acd4f7bdcd13bd412e232ed85d05f2a87dd5e2cdd04f9d219f88b569fc`。本页说明工程材料覆盖情况，不代替竞赛评审或报名后台审核。

| 要求 | 工程材料 | 状态 |
|---|---|---|
| 纯英文、小写路径 | 全部受跟踪路径与链接检查，历史路径有[映射表](../verification/path_migration_20261102.json) | 公开树采用小写路径，代表性实测数据与样例副本的身份分别记录 |
| 完整源码与上板入口 | [RTL与构建](../build/readme.md)、[板端驱动](../board/readme.md)、[Python参考](../sim/golden/readme.md) | 已提供 |
| 板级输出与参考比对 | [8窗FPGA复核](../data/review/hx_fpga_replay_20261101/independent_review.json)、[实验结果](results.md) | 已提供实测与离线核对，适用范围逐项注明 |
| 仿真或验证结果 | Python测试、保存的FPGA输出、[CI](../.github/workflows/validate.yml) | 自动验证入口已提供，实际运行状态见Actions |
| 资源、时钟、关键性能 | [实现报告](../build/reports/rebuild_20261102/readme.md)、[结果与性能](results.md) | 两套100MHz实现，保留时序和DRC报告 |
| 从零构建 | [依赖获取与Vivado命令](hardware.md)、[构建身份](../verification/hardware_rebuild_20261102.json) | 2026-11-02两套干净构建已完成；当前硬件输入按SHA核对 |
| 优化前后比较 | [技术报告第11章](../report/design_report.md#11-性能资源与优化)、[原始PC基线](../data/benchmarks/preparation.json) | Windows PC 上准备耗时 13.0757s→1.0532s，解析 17→1，8 窗输入相同。板端有分段耗时，尚缺同口径优化前后基线 |
| 背景、原理、接口与复现 | [报告索引](../report/readme.md) | 竞赛提交版已定稿并导出PDF，仓库另提供完整技术正文 |
| 提示词、回答与纠错轨迹 | [AI案例](ai_collaboration.md)、[原文节选与SHA](ai_excerpts.json) | 已补4组必要原文链路 |
| Skill适用范围、使用与失效 | [技能包](../skill/readme.md) | 已提供工作流、案例与边界；未宣称跨板实测 |
| 初级组年级资格 | 队长于2026-11-02确认：全体队员均为大一至大三在读本科生 | 队长已确认；正式资格以后台审核为准 |
| 公开仓库与允许再分发许可 | [许可说明](licensing.md)、[第三方来源](../third_party_notices.md) | 独立公开仓库，团队原创代码和文档采用 MIT |

## 仍需团队完成的交付项

- 演示视频、授权材料及竞赛网站正式提交。

以上事项不影响已提供源码和已有实测记录的核查，但在完成前不将整个参赛交付标为全部完成。若进入决赛，海报按指南第六章采用AMD模板、一页英文且不含队伍身份信息。
