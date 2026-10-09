---
name: req-archive
description: 生成或修订需求Markdown、记录实施进度；新增、补充、撤回、用户报告完成时使用。
---

# req-archive

读取禁令。先info说明固定资产目录；全部产物只写独立资产，包和源码只读，不保存原话。
读取../../templates/bundle.example.json。填标准章节与规范化变更原因，通过apply --stdin交JSON，不存聊天/临时输入文件。
修订读最新版，保留ID/首次时间/来源/证据，使用expected_revision。需求状态与progress独立；未知用待核实，已完成需用户报告或只读完成依据，禁止运行代码验证。
apply自动生成总览、索引和周总汇，再validate；回报需求和周表路径。
