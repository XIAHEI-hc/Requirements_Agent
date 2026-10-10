---
name: req-route
description: 识别项目归属、拆分混合目标与大型需求子项；待归属、查重、补充或大需求拆解时使用。
---

# req-route

读取 ../../agent/PROHIBITIONS.md 和 CORE.md，只读代码，不保存原话。读取固定资产的项目登记、需求总览、项目归属索引与规范记录。
按可独立验收的目标识别顶层需求；大型同一目标保留一个父条目，拆 children 子项，各自有 title、acceptance。歧义项目用 unassigned，明确项继续。项目仅为归属字段，不建项目目录。
已有目标补充保持原 ID。跨项目独立条目用真实已存在 ID 关联。纠正项目在原 ID 新修订，更新归属依据，不移动文件；当前不适用的代码证据清除，历史仍保留。
子项自动编号 父ID-SNN；修订列表保留所有旧子项及其 ID，不删子项，撤销改已取消。不能把父项进度自动应用于子项，需明确覆盖范围和各自依据。结构见 ../../templates/large-requirement.example.json。
