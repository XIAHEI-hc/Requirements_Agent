"""Fictional workweek demo; all runtime assets are temporary."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
import importlib.util
import tempfile
import json
import copy
import contextlib
import io

BASE=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('reqmgr',BASE/'scripts/reqmgr.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
original_now=m.now
clock='2026-09-22T20:00:00+08:00'
m.now=lambda:clock
try:
    with tempfile.TemporaryDirectory(prefix='reqmgr-v3-demo-') as tmp:
        home=Path(tmp);sources=home/'sources';sources.mkdir()
        projects=[]
        for key,name in [('pixlab','PixLab'),('market','工具市场')]:
            p=sources/key;p.mkdir();(p/'README.md').write_text('fictional demo',encoding='utf-8')
            projects.append(dict(id=key,name=name,path=str(p)))
        registry=home/'projects.json';registry.write_text(json.dumps(projects),encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()):m.init_asset(home/'assets',[sources],registry)
        ws=m.Workspace(home/'assets')
        template=m.read_json(BASE/'templates/bundle.example.json')['requirements'][0]
        def new(title,key='pixlab',**extra):
            item=copy.deepcopy(template);item.update(title=title,project_id=key,**extra)
            item['sections']['需求描述']='虚构示例：'+title
            return item
        def apply(items):
            with contextlib.redirect_stdout(io.StringIO()):
                m.apply_bundle(ws,dict(received_at=clock,time_basis='平台消息时间',requirements=items))
        def resolve(ident,children=False):
            r=next(r for r in ws.records() if r['id']==ident)
            item=dict(id=ident,expected_revision=r['revision'],reason='登记用户反馈的验收结果摘要',status='已解决',progress_source='用户报告',progress_note='用户反馈目标已验收，未独立验证',completion_evidence=['虚构用户反馈：目标行为已验收的规范化摘要'])
            if children:item['children']=[dict(id=c['id'],status='已解决',progress_source='用户报告',progress_note='用户明确反馈此子项验收完成，未独立验证',completion_evidence=['此子项目标已验收的规范化摘要']) for c in r['children']]
            apply([item])
        apply([new('项目配置与后处理改进','market',children=[dict(title='项目配置',acceptance='可保存并选择配置'),dict(title='后处理',acceptance='可根据配置输出后处理结果')])])
        clock='2026-09-28T21:20:29+08:00'
        apply([new('梳理工作周归档方案','unassigned'),new('评估双图对比优化空间'),new('理清只读代码与需求管理边界','unassigned'),new('讨论部署方案','unassigned',requirement_date='2026-10-09',archive_week='2026-10-05')])
        clock='2026-09-29T10:59:07+08:00'
        resolve('20260922-01',children=True);resolve('20260928-03')
        clock='2026-10-08T09:26:34+08:00'
        resolve('20260928-01');resolve('20260928-02')
        with contextlib.redirect_stdout(io.StringIO()):m.validate(ws)
        summary=ws.path('09月/09.28-10.02/本周需求总汇.md').read_text(encoding='utf-8')
        child_summary=ws.path('09月/09.21-09.25/本周需求总汇.md').read_text(encoding='utf-8')
        print(summary)
        print('虚构演示校验通过；提前建档需求计入 10.05-10.09，子需求计入父项原周，9月28日周仅含3项新需求。')
        print('演示资产位于系统临时目录，结束后清理；没有业务实施、原话存档或包内产物。')
finally:
    m.now=original_now
