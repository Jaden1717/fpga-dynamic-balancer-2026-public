# 第三方来源与处理

| 内容 | 来源 | 获取方式 |
|---|---|---|
| PYNQ-Z2 板卡定义与图片 | TUL 原厂下载包 | [依赖清单](third_party/pynq_z2_board_files.json)记录 URL 与 SHA256。按[硬件构建说明](docs/hardware.md)下载并校验 |
| PS7 配置 | TUL 板卡预置 | 由原厂 `preset.xml` 在构建机器本地生成 |
| Vivado 位流、XSA 与工具 | AMD Vivado 2026.1 | 使用工具链在本地构建。历史上板使用的封存产物身份见[封存清单](sealed_manifest.json) |
| PYNQ、NumPy、pytest | 对应上游及 [requirements-dev.txt](requirements-dev.txt) | 按上游方式安装 |
| GitHub Actions | checkout 和 setup-python | 工作流引用上游固定提交 |

团队的 MIT 授权涵盖其原创代码与文档。原厂文件及生成产物按其各自权利条款处理。
