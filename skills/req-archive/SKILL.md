---
name: req-archive
description: 创建或修订按工作周归档的Markdown和父子需求状态；新增、补充、项目纠正或进度反馈时使用。
---

# req-archive

读取 ../../agent/CORE.md 和 PROHIBITIONS.md，先 info 核实独立资产。只写资产，不写包/源码、不保存原话。
读 ../../templates/bundle.example.json 和 ../../docs/06_工具命令与恢复.md，通过 apply --stdin 传规范 JSON，不创建输入暂存文件。新增含 title、reason 和完整 sections；主状态 progress 用五种状态，也可 status，不能矛盾。用户明确指定日期/周时设置 requirement_date、archive_week，实际建档时间由工具生成。
修订读最新记录，带 id、expected_revision 和规范化 reason；省略字段保留。普通修订不能改编号日期/归档周/文件路径。文件为 MM月/MM.DD-MM.DD/MM.DD-NN-标题.md；项目只是字段。
状态变化须更新 progress_source、progress_note。已解决还需 completion_evidence 摘要/只读引用，注明未独立验证；严禁执行代码核验。已确认不等于已解决。
大型需求用 children。新子项 title、acceptance；修订用原子项 id，列表含所有既有子项。不删除历史子项，取消记依据。父项解决前有效子项都需明确已解决，不自动互推。整体反馈明确覆盖各子项时逐项登记摘要，再更新父项。
apply 自动刷新总览、项目归属索引、全部周总汇与周索引。validate 后回报资产路径、需求编号与原归档周总汇；跨周状态更新同时报更新周总汇。
