import sys
sys.dont_write_bytecode=True
from pathlib import Path
import importlib.util
import tempfile
import json
import copy

BASE=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('reqmgr',BASE/'scripts/reqmgr.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

with tempfile.TemporaryDirectory(prefix='reqmgr-v2-demo-') as tmp:
    home=Path(tmp);sources=home/'sources';sources.mkdir();p=sources/'pixlab';p.mkdir();(p/'README.md').write_text('fictional demo',encoding='utf-8')
    registry=home/'projects.json';registry.write_text(json.dumps([dict(id='pixlab',path=str(p))]),encoding='utf-8')
    m.init_asset(home/'assets',[sources],registry);ws=m.Workspace(home/'assets')
    b=m.read_json(BASE/'templates/bundle.example.json');b['received_at']=m.now();b['time_basis']='系统接收时间'
    item=copy.deepcopy(b['requirements'][0]);item['title']='另一项需求演示';item.update(progress='未完成',progress_source='用户报告',progress_note='用户报告进行中，未独立验证')
    b['requirements'].append(item);m.apply_bundle(ws,b)
    r=next(r for r in ws.records() if r['title']=='另一项需求演示')
    item.update(id=r['id'],expected_revision=r['revision'],progress='已完成',progress_source='用户报告',progress_note='用户报告已完成，未独立验证',completion_evidence=['用户报告目标行为已检查；演示摘要'],reason='记录用户完成报告摘要')
    m.apply_bundle(ws,dict(received_at=m.now(),time_basis='系统接收时间',requirements=[item]));m.validate(ws)
    print(ws.path('需求总览.md').read_text(encoding='utf-8'))
    print('演示全部在系统临时目录，未向工具包或真实源码写入；即将清理临时演示资产。')
