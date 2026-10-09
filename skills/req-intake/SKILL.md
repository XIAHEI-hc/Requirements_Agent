---
name: req-intake
description: 理解自然语言与真实消息时间，不落盘原话；用户自由表达或发来混合需求时使用。
---

# req-intake

先读取../../agent/PROHIBITIONS.md。严禁代码改造，严禁保存原话。
核实固定资产路径，缺失先询问，不默认本包。用当前对话理解目标和条件，获取平台消息时间/用户提供时间，否则系统接收时间。
不写raw.txt、intake或原话哈希。直接转req-route；输出为整理后的条目和时间字段。
