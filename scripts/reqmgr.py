#!/usr/bin/env python3
"""Requirement document manager. Standard library only; never writes to sources."""
import argparse
import contextlib
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import html
import uuid

BASE = Path(__file__).resolve().parents[1]
TZ = dt.timezone(dt.timedelta(hours=8))
PROGRESS = ('未开始', '进行中', '已阻塞', '已解决', '已取消')
LEGACY_PROGRESS = {'未完成': '进行中', '已完成': '已解决', '待核实': '未开始'}
TIME_BASES = ('用户提供', '平台消息时间', '系统接收时间')
STATUSES = ('待澄清', '待确认', '已确认', '暂缓', '已撤回', '已合并')
PRIORITIES = ('P0', 'P1', 'P2', 'P3', '待评估')
SECTIONS = ('需求描述', '归属依据', '当前现状', '目标行为', '范围与不包含', '验收标准', '优先级理由', '待澄清问题')

def now():
    return dt.datetime.now(TZ).isoformat(timespec='seconds')

def timestamp(value):
    t = dt.datetime.fromisoformat(value)
    if t.tzinfo is None:
        raise ValueError('时间必须携带时区，例如 2026-10-09T22:53:35+08:00')
    return t.astimezone(TZ)

def bucket(value):
    return week_folder(timestamp(value).date().isoformat())

def date_value(value):
    return dt.date.fromisoformat(value).isoformat()

def workweek(value):
    d=dt.date.fromisoformat(value[:10])
    return d-dt.timedelta(days=d.weekday())

def week_folder(date):
    monday=workweek(date);friday=monday+dt.timedelta(days=4)
    return Path(monday.strftime('%m月')) / (monday.strftime('%m.%d')+'-'+friday.strftime('%m.%d'))

def archive_folder(ws,date):
    monday=workweek(date)
    # The configured base year uses exactly 月/周. Later/earlier years are isolated.
    prefix=Path('.') if monday.year==ws.cfg['archive_year'] else Path(f'{monday.year}年')
    return prefix/week_folder(date)

def pretty_time(value):
    text=timestamp(value).strftime('%Y-%m-%d %H:%M:%S %z')
    return text[:-2]+':'+text[-2:]


def safe_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', value):
        raise ValueError('ID 仅允许小写英文、数字和短横线，最长64字符')
    return value

def title_file(value):
    value = re.sub(r'[<>:"/\\|?*()#%\x00-\x1f]', '_', value).strip(' .')[:60]
    value = re.sub(r'\s+', '-', value)
    return value or '未命名需求'

def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))

def atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.reqtmp-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)

def outside_package(asset):
    if asset.is_relative_to(BASE) or BASE.is_relative_to(asset):
        raise ValueError('固定需求资产目录必须与Agent包彼此分离，不可相互包含')

def asset_path(value):
    path=Path(value)
    if not path.is_absolute():
        raise ValueError('必须明确指定固定资产目录的绝对路径；不默认写入本包或当前目录')
    return path.resolve()

class Workspace:
    def __init__(self, root):
        self.out=asset_path(root)
        outside_package(self.out)
        self.config_path=self.out/'.reqmgr/config.json'
        self.cfg=read_json(self.config_path)
        if self.cfg.get('version')!=3 or self.cfg.get('timezone')!='Asia/Shanghai':
            raise ValueError('需要v3配置和Asia/Shanghai时区；v2请先migrate-v2到独立空资产')
        if Path(self.cfg['asset_root']).resolve()!=self.out:
            raise ValueError('资产路径已变化，需先按迁移说明显式更新路径，不得静默建立第二套资产')
        self.roots=[asset_path(p) for p in self.cfg['source_roots']]
        if not self.roots or any(not p.is_dir() for p in self.roots):
            raise ValueError('源码根目录必须实际存在')
        for p in self.roots:
            if self.out.is_relative_to(p) or p.is_relative_to(self.out):
                raise ValueError('需求资产与源码根目录必须彼此分离')
        self.projects={}
        for item in self.cfg.get('projects',[]):
            key=safe_id(item['id'])
            if key=='unassigned' or key in self.projects:
                raise ValueError('项目ID重复或保留')
            p=asset_path(item['path'])
            if not any(p.is_relative_to(r) for r in self.roots):
                raise ValueError('项目路径超出指定源码根目录')
            self.projects[key]=dict(item,path=str(p))

    def path(self, rel):
        if Path(rel).is_absolute():
            raise ValueError('资产内部路径必须是相对路径')
        p=self.out/rel;r=p.resolve()
        if not r.is_relative_to(self.out) or r.is_relative_to(BASE) or any(r.is_relative_to(s) for s in self.roots):
            raise ValueError('拒绝源码/包写入、目录穿越或外部符号链接')
        return p

    def write(self,rel,text):atomic(self.path(rel),text)
    def json(self,rel,obj):self.write(rel,json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
    @contextlib.contextmanager
    def lock(self):
        p=self.path('.reqmgr/writer.lock');p.parent.mkdir(parents=True,exist_ok=True)
        fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        try:
            os.write(fd,f'pid={os.getpid()}\ntime={now()}'.encode());os.close(fd);yield
        finally:p.unlink()
    def records(self):
        return [read_json(self.path(p.relative_to(self.out))) for p in sorted(self.path('.reqmgr/records').glob('*.json'))]
    def revisions(self,ident):
        return [read_json(self.path(p.relative_to(self.out))) for p in sorted(self.path(Path('.reqmgr/revisions')/ident).glob('r*.json'))]
    def audit(self,event):
        p=self.path('.reqmgr/changes.jsonl')
        with p.open('a',encoding='utf-8') as f:f.write(json.dumps(dict(at=now(),**event),ensure_ascii=False)+'\n')

def init_asset(root,source_roots,projects_file=None):
    out=asset_path(root);outside_package(out)
    sources=[asset_path(p) for p in source_roots]
    if not sources or any(not p.is_dir() for p in sources):raise ValueError('需要实际存在的源码根目录')
    if any(out.is_relative_to(s) or s.is_relative_to(out) for s in sources):raise ValueError('源码与需求资产不可相互包含')
    if out.exists() and any(out.iterdir()):raise ValueError('初始化仅允许新的空目录，已有资产使用info')
    items=read_json(projects_file) if projects_file else []
    if not isinstance(items,list):raise ValueError('projects-file必须为项目数组')
    # Validate project data before creating any asset files.
    keys=set()
    for i in items:
        key=safe_id(i['id']);p=asset_path(i['path'])
        if key=='unassigned' or key in keys or not any(p.is_relative_to(s) for s in sources):raise ValueError('无效项目登记')
        keys.add(key)
    cfg=dict(version=3,archive_year=timestamp(now()).year,timezone='Asia/Shanghai',asset_id='ASSET-'+uuid.uuid4().hex[:12],asset_root=str(out),source_roots=[str(s) for s in sources],projects=items)
    # No fallback directory exists: explicit root is required.
    atomic(out/'.reqmgr/config.json',json.dumps(cfg,ensure_ascii=False,indent=2)+'\n')
    ws=Workspace(out)
    with ws.lock():
        ws.write('资产说明.md', '# 固定需求管理资产\n\n资产ID：'+cfg['asset_id']+'\n\n固定目录：`'+str(out)+'`\n\n源码根目录：'+', '.join(str(p) for p in sources)+'\n\nAgent包可以单独升级，长期资产不放入包内。日常从需求总览.md和每周总汇索引.md查看；不保存原话。\n')
        rebuild(ws)
    info(ws)

def info(ws):
    emit(dict(agent_package=str(BASE),asset_root=str(ws.out),asset_id=ws.cfg['asset_id'],source_roots=[str(p) for p in ws.roots],
              configuration=str(ws.config_path),dashboard=str(ws.path('需求总览.md')),weekly_index=str(ws.path('每周总汇索引.md')),
              raw_input_saved=False,prohibition='严禁业务代码改造'))

def reject_unknown(obj,allowed,label):
    if not isinstance(obj,dict) or set(obj)-set(allowed):raise ValueError(label+'含未支持字段；禁止raw_input/original_text/原话等存档字段')

def progress_badge(value):
    icon={'未开始':'⚪','进行中':'🟡','已阻塞':'🔴','已解决':'🟢','已取消':'⚫'}[value]
    return f'`{icon} {value}`'

def project_name(ws,rec):
    key=rec['project_id']
    return ws.projects.get(key,{}).get('name',key) if key!='unassigned' else '无明确项目'


def md_cell(value):
    text=html.escape(str(value)).replace('|','／').replace('\n',' ').replace('\r',' ')
    return text.replace('[','\\[').replace(']','\\]').replace('`','&#96;')

def render_req(rec,rel):
    def link(target):return os.path.relpath(target,Path(rel).parent).replace(os.sep,'/')
    lines=[f'# {rec["id"]} {rec["title"]}','','| 字段 | 内容 |','|---|---|']
    for name,key in [('项目','project_id'),('首次提出','created_at'),('最近整理','updated_at'),('归档工作周','archive_week'),('解决时间','resolved_at'),('需求确认','status'),('建议优先级','priority'),('修订','revision')]:
        lines.append(f'| {name} | {md_cell(rec[key])} |')
    lines += [f'| 实施进度 | {progress_badge(rec["progress"])} |','', '**仅管理需求与进度记录；严禁本Agent修改业务代码、配置、数据库或部署。**','',
              '## 时间与记录依据','', '不保存用户原话。只记录消息时间、整理后的需求、变更原因和状态依据。']
    for e in rec['events']:lines.append(f'- {e["id"]} · {e["received_at"]} · {e["time_basis"]}')
    lines += ['','## 进度依据','',f'来源：{rec["progress_source"]}\n\n说明：{rec["progress_note"]}', '', '解决依据：']
    lines += ['- '+x for x in rec['completion_evidence']] or ['- 无；不得自动宣布已解决。']
    lines += ['','## 排序确认','',rec['priority_confirmation'] if rec['priority_confirmed'] else '当前仅为建议排序，未获用户确认。','', '## 代码核查依据','']
    lines += [f'- [{sid}]({link("代码依据/"+sid+".json")})' for sid in rec['source_ids']] or ['未提供源码核查证据；本地/远程/部署状态不据此认定。']
    for name in SECTIONS:lines += ['', '## '+name,'',rec['sections'][name]]
    lines += ['', '## 子需求', '', children_table(rec), '', '## 关联需求','',', '.join(rec['related_ids']) or '无','','## 本次变更原因','',rec['reason'],'']
    return '\n'.join(lines)

def children_table(rec):
    children=rec.get('children',[])
    if not children:return '暂无子需求。'
    lines=['| 子需求编号 | 标题 | 状态 | 验收标准 | 最后更新时间 | 解决时间 | 当前进展 |',
           '| --- | --- | --- | --- | --- | --- | --- |']
    for child in children:
        lines.append('| '+' | '.join((child['id'],md_cell(child['title']),progress_badge(child['progress']),
            md_cell(child['acceptance']),pretty_time(child['updated_at']),pretty_time(child['resolved_at']) if child['resolved_at'] else '—',
            md_cell(child['progress_source']+'：'+child['progress_note'])))+' |')
    return '\n'.join(lines)

def table(items,base,priority=False,ws=None):
    order={x:i for i,x in enumerate(PRIORITIES)}
    items=sorted(items,key=(lambda r:(r['progress']=='已取消',order[r['priority']],r['requirement_date'],r['daily_sequence'],r['id'])) if priority else (lambda r:(r['requirement_date'],r['daily_sequence'],r['id'])))
    lines=['| 编号 | 日期与标题 | 所属项目 | 状态 | 优先级 | 首次创建时间 | 最后更新时间 | 解决时间 | 当前进展 | 需求文件 |',
           '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for r in items:
        href=os.path.relpath(r['path'],base).replace(os.sep,'/')
        name=project_name(ws,r) if ws else r['project_id']
        progress=r['progress_source']+'：'+r['progress_note']
        if r.get('children'):progress+=f'；子需求 {sum(c["progress"]=="已解决" for c in r["children"])} / {sum(c["progress"]!="已取消" for c in r["children"])} 已解决'
        if r['related_ids']:progress+='；关联 '+', '.join(r['related_ids'])
        lines.append('| '+' | '.join((r['id'],md_cell(r['requirement_date']+' '+r['title']),md_cell(name),progress_badge(r['progress']),
            '未设置' if r['priority']=='待评估' else r['priority'],pretty_time(r['created_at']),pretty_time(r['updated_at']),
            pretty_time(r['resolved_at']) if r['resolved_at'] else '—',md_cell(progress),f'[打开]({href})'))+' |')
    return '\n'.join(lines)+'\n'

def week_bounds(date):
    start=workweek(date)
    # Events on Saturday/Sunday belong to the preceding Monday-Friday workweek.
    return dt.datetime.combine(start,dt.time(),TZ),dt.datetime.combine(start+dt.timedelta(days=7),dt.time(),TZ)

def week_state(ws,date,project=None):
    start,end=week_bounds(date);key=start.date().isoformat()
    records=[r for r in ws.records() if not project or r['project_id']==project]
    own=[r for r in records if r['archive_week']==key]
    cross=[]
    for r in records:
        if r['archive_week']==key:continue
        # Only revisions after initial filing are cross-week advancement events.
        revisions=ws.revisions(r['id'])
        for previous,v in zip(revisions,revisions[1:]):
            if workweek(v['updated_at']).isoformat()==key:
                cross.append((r,dict(v,previous_progress=previous['progress'])))
    return dict(start=start,end=end,all=records,new=own,cross=sorted(cross,key=lambda x:(x[1]['updated_at'],x[0]['id'])))

def weekly_report(ws,date,project=None):
    st=week_state(ws,date,project);start=st['start'].date();friday=start+dt.timedelta(days=4)
    label=week_folder(date).name;rel=archive_folder(ws,date)/'本周需求总汇.md';base=rel.parent
    own=st['new'];children=[c for r in own for c in r.get('children',[]) if c['progress']!='已取消']
    counts=[sum(r['progress']==x for r in own) for x in PROGRESS]
    lines=[f'# {label} 本周需求总汇','',f'工作周：{start} 至 {friday}','',
        f'最后同步时间：{pretty_time(now())}（同步 {len(own)} 项归档需求的最新状态；跨周推进 {len(st["cross"])} 次）','',
        '## 状态统计','', '| 顶层需求 | 有效子需求 | 未开始 | 进行中 | 已阻塞 | 已解决 | 已取消 |',
        '| ---: | ---: | ---: | ---: | ---: | ---: | ---: |',
        '| '+ ' | '.join(map(str,[len(own),len(children)]+counts))+' |','',
        '> 统计按指定归档周计入顶层需求；五个状态数只统计顶层需求，不与子需求重复相加。有效子需求不含已取消项，子需求状态见下表。往周需求的推进不改变本周新增数量；本表持续同步最新状态，历史时点可查修订历史。','',
        '状态图例：'+' / '.join(progress_badge(x) for x in PROGRESS),'',
        '## 本周完整需求','',table(own,base,ws=ws),'','## 大型需求与子需求进度','']
    large=[r for r in own if r.get('children')]
    if not large:lines.append('本周暂无大型需求。')
    for r in large:
        href=os.path.relpath(r['path'],base).replace(os.sep,'/')
        lines.extend([f'### {r["id"]} {md_cell(r["title"])}','',f'父需求：[打开]({href})；状态：{progress_badge(r["progress"])}','',children_table(r),''])
    lines.extend(['','## 跨周补充与推进',''])
    if not st['cross']:lines.append('本周暂无跨周补充与推进。')
    for r,v in st['cross']:
        href=os.path.relpath(r['path'],base).replace(os.sep,'/')
        transition=('状态由 '+progress_badge(v['previous_progress'])+' 改为 '+progress_badge(v['progress'])) if v['previous_progress']!=v['progress'] else '状态保持 '+progress_badge(v['progress'])
        lines.append(f'- **{r["id"]}**（{md_cell(v["title"])}，{md_cell(project_name(ws,v))}，属 {week_folder(r["archive_week"]).name} 周）：{pretty_time(v["updated_at"])}；{transition}；{md_cell(v["reason"])}。{md_cell(v["progress_source"]+"："+v["progress_note"])}。仅登记，AI 未实施开发。详见 [需求文件]({href})。')
        if v.get('children'):
            lines.append('  子需求快照：'+'；'.join(c['id']+' '+progress_badge(c['progress']) for c in v['children'])+'。')
    ws.write(rel,'\n'.join(lines)+'\n');return rel

def rebuild(ws,extra_week=None):
    records=ws.records();weeks=set(ws.cfg.get('report_weeks',[]))
    weeks.add(workweek(now()).isoformat())
    if extra_week:weeks.add(workweek(extra_week).isoformat())
    for r in records:
        weeks.add(r['archive_week'])
        weeks.update(workweek(v['updated_at']).isoformat() for v in ws.revisions(r['id']))
    legend='状态图例：'+' / '.join(progress_badge(x) for x in PROGRESS)
    ws.write('需求总览.md','# 需求总览\n\n固定资产目录：`'+str(ws.out)+'`\n\n[每周需求总汇索引](每周总汇索引.md)\n\n'+legend+'\n\n'+table(records,Path('.'),ws=ws))
    # Project affiliation is only a field/grouped table. No project archive folders.
    project_lines=['# 按项目排序的需求索引','',legend,'']
    keys=sorted(set(ws.projects)|{r['project_id'] for r in records})
    for key in keys:
        group=[r for r in records if r['project_id']==key]
        label=ws.projects.get(key,{}).get('name',key) if key!='unassigned' else '无明确项目'
        project_lines+=['## '+md_cell(label),'',table(group,Path('.'),True,ws)]
    ws.write('项目归属索引.md','\n'.join(project_lines)+'\n')
    links=[]
    for date in sorted(weeks,reverse=True):
        path=weekly_report(ws,date)
        links.append(f'- [{date[:4]}年 {path.parent.name}]({path.as_posix()})')
    ws.write('每周总汇索引.md','# 每周需求总汇索引\n\n归档周查看最新状态；跨周推进不计为新增。\n\n'+'\n'.join(links)+'\n')
    ws.cfg['report_weeks']=sorted(weeks)
    ws.json('.reqmgr/config.json',ws.cfg)


def progress_fields(item,previous,changed_at):
    old=previous or {}
    if 'progress' in item and (not isinstance(item['progress'],str) or not item['progress']):raise ValueError('progress须为有效状态文字')
    supplied=item.get('progress',item.get('status') if item.get('status') in PROGRESS else None)
    progress=supplied or old.get('progress','未开始')
    # Legacy input aliases are accepted, but all persisted/displayed states are v3.
    progress=LEGACY_PROGRESS.get(progress,progress)
    source=item.get('progress_source',old.get('progress_source','新需求初始化'))
    note=item.get('progress_note',old.get('progress_note','新录入需求默认未开始；不是代码状态核查结论'))
    evidence=item.get('completion_evidence',old.get('completion_evidence',[]))
    if progress not in PROGRESS or not isinstance(source,str) or not source.strip() or not isinstance(note,str) or not note.strip():
        raise ValueError('状态与依据必填')
    if previous and progress!=old['progress'] and not all(k in item for k in ('progress_source','progress_note')):
        raise ValueError('改变状态需显式更新进度来源和说明')
    if progress!='未开始' and source=='新需求初始化':raise ValueError('初始化依据只能用于未开始')
    if not isinstance(evidence,list) or any(not isinstance(x,str) or not x.strip() for x in evidence):raise ValueError('解决依据应为摘要/引用数组')
    if progress=='已解决' and (not evidence or source in ('新需求初始化','未核实')):raise ValueError('已解决必须有用户报告或只读证据及解决依据')
    resolved=old.get('resolved_at') if old.get('progress')=='已解决' else None
    if progress=='已解决':resolved=resolved or changed_at
    else:resolved=None
    return dict(progress=progress,progress_source=source,progress_note=note,completion_evidence=evidence,resolved_at=resolved)

def prepare_children(parent,item,previous,changed_at):
    old={c['id']:c for c in (previous or {}).get('children',[])}
    if 'children' not in item:return list(old.values())
    children=item['children']
    if not isinstance(children,list):raise ValueError('children应为子需求数组')
    result=[];used=set();sequence=max([int(k.rsplit('-S',1)[1]) for k in old]+[0])
    for child in children:
        reject_unknown(child,('id','title','acceptance','status','progress','progress_source','progress_note','completion_evidence'),'子需求')
        if 'status' in child and child['status'] not in PROGRESS:raise ValueError('子需求status须为五种主状态之一')
        if child.get('status') in PROGRESS and 'progress' in child and child['status']!=LEGACY_PROGRESS.get(child['progress'],child['progress']):raise ValueError('子需求status与progress互相冲突')
        ident=child.get('id')
        if ident:
            if ident not in old:raise ValueError('修订子需求ID不存在于该父需求')
            prior=old[ident]
        else:
            sequence+=1;ident=parent+f'-S{sequence:02d}';prior=None
        if ident in used:raise ValueError('子需求ID重复')
        used.add(ident)
        title=child.get('title',(prior or {}).get('title',''));acceptance=child.get('acceptance',(prior or {}).get('acceptance',''))
        if not isinstance(title,str) or not title.strip() or len(title)>200 or '\n' in title or '\r' in title:raise ValueError('子需求标题需200字符内单行文字')
        if not isinstance(acceptance,str) or not acceptance.strip():raise ValueError('子需求必须有独立验收标准')
        fields=progress_fields(child,prior,changed_at)
        candidate=dict(id=ident,title=title,acceptance=acceptance,created_at=(prior or {}).get('created_at',changed_at),updated_at=changed_at,**fields)
        if prior and all(candidate[k]==prior[k] for k in candidate if k!='updated_at'):candidate['updated_at']=prior['updated_at']
        result.append(candidate)
    if set(old)-used:raise ValueError('禁止移除已有子需求；不再需要的子需求改为已取消并记录依据')
    return result

def allocate_id(old,date,seen):
    prefix=date.replace('-','')+'-'
    numbers=[int(k[len(prefix):]) for k in list(old)+list(seen) if re.fullmatch(re.escape(prefix)+r'\d+',k)]
    numbers += [r.get('daily_sequence',0) for r in old.values() if r['requirement_date']==date]
    return prefix+f'{max(numbers+[0])+1:02d}'

def prepare(ws,bundle):
    reject_unknown(bundle,('received_at','time_basis','requirements'),'批次')
    received=timestamp(bundle.get('received_at') or now()).isoformat(timespec='seconds');changed_at=now()
    basis=bundle.get('time_basis') or ('用户提供' if bundle.get('received_at') else '系统接收时间')
    if basis not in TIME_BASES or not bundle.get('received_at') and basis!='系统接收时间':raise ValueError('无效时间依据')
    items=bundle['requirements']
    if not isinstance(items,list) or not items:raise ValueError('requirements必须是非空数组')
    old={r['id']:r for r in ws.records()};seen=set();prepared=[]
    event=dict(id='EV-'+uuid.uuid4().hex[:12],received_at=received,time_basis=basis)
    allowed=('id','expected_revision','project_id','title','status','priority','priority_confirmed','priority_confirmation','source_ids','related_ids','reason','sections','progress','progress_source','progress_note','completion_evidence','requirement_date','archive_week','children')
    for item in items:
        reject_unknown(item,allowed,'需求');ident=item.get('id');previous=None
        if ident:
            if ident not in old or item.get('expected_revision')!=old[ident]['revision']:raise ValueError('修订ID不存在或expected_revision冲突')
            previous=old[ident]
        prior=previous or {}
        key=item.get('project_id',prior.get('project_id','unassigned'))
        if key!='unassigned' and key not in ws.projects:raise ValueError('项目未登记；不明确时用unassigned')
        title=item.get('title',prior.get('title',''))
        if not isinstance(title,str) or not title.strip() or len(title)>200 or '\n' in title or '\r' in title:raise ValueError('需求标题需为200字符内单行文字')
        title=title.strip()
        date=date_value(item.get('requirement_date',prior.get('requirement_date',received[:10])))
        archive=workweek(item.get('archive_week',prior.get('archive_week',date))).isoformat()
        if previous and (date!=previous['requirement_date'] or archive!=previous['archive_week']):raise ValueError('归档日期和工作周已固定；不得通过普通修订移动归档')
        if not ident:ident=allocate_id(old,date,seen)
        if ident in seen:raise ValueError('同批次重复修订')
        seen.add(ident)
        status=item.get('status',prior.get('status','待澄清'))
        if status in PROGRESS:status=prior.get('status','待澄清')
        priority=item.get('priority',prior.get('priority','待评估'))
        if status not in STATUSES or priority not in PRIORITIES:raise ValueError('无效需求确认状态或优先级')
        if item.get('status') in PROGRESS and 'progress' in item and item['status']!=LEGACY_PROGRESS.get(item['progress'],item['progress']):raise ValueError('status与progress互相冲突')
        reason=item.get('reason','')
        if not isinstance(reason,str) or not reason.strip():raise ValueError('需要整理后的新增/修订原因，不要复制原话')
        sections=item.get('sections',prior.get('sections'));reject_unknown(sections,SECTIONS,'需求章节')
        if any(not isinstance(sections.get(x),str) or not sections[x].strip() for x in SECTIONS):raise ValueError('统一需求章节必填；未知写待核实')
        fields=progress_fields(item,previous,changed_at)
        children=prepare_children(ident,item,previous,changed_at)
        if fields['progress']=='已解决' and any(c['progress'] not in ('已解决','已取消') for c in children):
            raise ValueError('父需求已解决前需明确记录所有有效子需求已解决；不得推断子项完成')
        sids=item.get('source_ids',prior.get('source_ids',[]) if key==prior.get('project_id',key) else [])
        if not isinstance(sids,list):raise ValueError('source_ids须为数组')
        for sid in sids:
            if not isinstance(sid,str) or not re.fullmatch(r'SRC-[a-f0-9]{12}',sid):raise ValueError('无效源码证据ID')
            if read_json(ws.path('代码依据/'+sid+'.json'))['project_id']!=key:raise ValueError('禁止跨项目引用源码现状证据')
        related=item.get('related_ids',prior.get('related_ids',[]))
        if not isinstance(related,list) or any(x not in old for x in related):raise ValueError('关联需为真实已存在需求ID')
        confirmed=item.get('priority_confirmed',prior.get('priority_confirmed',False));confirmation=item.get('priority_confirmation',prior.get('priority_confirmation',''))
        if not isinstance(confirmed,bool) or not isinstance(confirmation,str) or confirmed and not confirmation.strip():raise ValueError('用户确认排序需说明确认依据')
        if previous and priority!=previous['priority'] and 'priority_confirmed' not in item:confirmed=False;confirmation=''
        rel=prior.get('path')
        if not rel:rel=(archive_folder(ws,archive)/(date[5:].replace('-','.')+'-'+ident.rsplit('-',1)[1]+'-'+title_file(title)+'.md')).as_posix()
        rec=dict(id=ident,project_id=key,title=title,created_at=prior.get('created_at',changed_at),updated_at=changed_at,status=status,priority=priority,priority_confirmed=confirmed,priority_confirmation=confirmation,
            revision=prior.get('revision',0)+1,events=prior.get('events',[])+[event],source_ids=sids,sections=sections,related_ids=related,
            reason=reason.strip(),path=rel,requirement_date=date,archive_week=archive,
            daily_sequence=prior.get('daily_sequence',int(ident.rsplit('-',1)[1]) if re.fullmatch(r'\d{8}-\d+',ident) else 0),children=children,**fields)
        # Preserve legacy provenance across subsequent revisions.
        for field in ('legacy_id','legacy_revision','legacy_updated_at'):
            if field in prior:rec[field]=prior[field]
        history=Path('修订历史')/ident/f'r{rec["revision"]:04}.md';revjson=Path('.reqmgr/revisions')/ident/f'r{rec["revision"]:04}.json'
        for dest in (rel,Path('.reqmgr/records')/(ident+'.json'),history,revjson):ws.path(dest)
        if ws.path(history).exists() or ws.path(revjson).exists():raise ValueError('历史版次已存在，禁止覆盖')
        prepared.append(rec)
    return prepared


def commit_records(ws,records,action='apply'):
    operation='OP-'+uuid.uuid4().hex[:12]
    journal=dict(id=operation,status='pending',action=action,requirement_ids=[r['id'] for r in records],at=now())
    ws.json('.reqmgr/operations/'+operation+'.json',journal)
    for rec in records:
        history=Path('修订历史')/rec['id']/f'r{rec["revision"]:04}.md'
        ws.write(history,render_req(rec,history));ws.write(rec['path'],render_req(rec,rec['path']))
        ws.json(Path('.reqmgr/revisions')/rec['id']/f'r{rec["revision"]:04}.json',rec)
        ws.json(Path('.reqmgr/records')/(rec['id']+'.json'),rec)
        ws.audit(dict(action=action,requirement_id=rec['id'],revision=rec['revision'],reason=rec['reason'],progress=rec['progress'],operation=operation))
    rebuild(ws);journal['status']='complete';ws.json('.reqmgr/operations/'+operation+'.json',journal)
    return operation

def apply_bundle(ws,bundle):
    with ws.lock():
        records=prepare(ws,bundle);op=commit_records(ws,records)
    emit(dict(asset_root=str(ws.out),operation=op,raw_input_saved=False,requirements=[dict(id=r['id'],revision=r['revision'],path=str(ws.path(r['path']))) for r in records]))

def validate(ws):
    errors=[]
    for forbidden in ('原始输入','intakes','inbox','staging'):
        if ws.path(forbidden).exists():errors.append('资产存在不应生成的原话/暂存目录：'+forbidden)
    for r in ws.records():
        rel=r['path'];history=f'修订历史/{r["id"]}/r{r["revision"]:04}.md'
        for dest in (rel,history):
            if not ws.path(dest).is_file():errors.append('缺少文档：'+dest)
            elif ws.path(dest).read_text(encoding='utf-8')!=render_req(r,dest):errors.append('文档与记录不一致：'+dest)
        revisions=ws.revisions(r['id'])
        if not revisions or revisions[-1]!=r:errors.append('当前记录与修订快照不一致：'+r['id'])
        if r['progress']=='已解决' and not r['completion_evidence']:errors.append('完成缺少依据：'+r['id'])
    for p in ws.path('.reqmgr/operations').glob('*.json'):
        if read_json(ws.path(p.relative_to(ws.out)))['status']!='complete':errors.append('存在中断批次：'+p.name)
    emit(dict(ok=not errors,requirements=len(ws.records()),asset_root=str(ws.out),raw_input_saved=False,errors=errors))
    if errors:raise ValueError('校验未通过')

def migration_plan(ws,old_root,version):
    old=asset_path(old_root)
    if old==ws.out or old.is_relative_to(ws.out) or ws.out.is_relative_to(old):raise ValueError('新旧资产目录必须彼此分离')
    if ws.records():raise ValueError('迁移目标已有需求；导入只允许新初始化的空资产')
    metadata=old/'.reqmgr' if version==2 else old
    if (metadata/'writer.lock').exists():raise ValueError('旧资产正在写入，请稍后重试迁移')
    if version==2 and read_json(metadata/'config.json').get('version')!=2:raise ValueError('迁移源不是v2资产')
    paths=sorted((metadata/'records').glob('REQ-*.json'))
    if not paths:raise ValueError('旧资产无records，禁止猜测导入对象')
    current=[read_json(p) for p in paths];plans=[];sources={};daily={}
    for item in sorted(current,key=lambda r:(r['created_at'],r['id'])):
        key=item['project_id'];ident=item['id']
        if key!='unassigned' and key not in ws.projects:raise ValueError('先登记旧资产中使用的项目ID：'+key)
        if not re.fullmatch(r'REQ-[A-Z0-9-]+',ident):raise ValueError('旧需求ID无效')
        date=timestamp(item['created_at']).date().isoformat();archive=workweek(date).isoformat()
        daily[date]=daily.get(date,0)+1
        rel=(archive_folder(ws,archive)/(date[5:].replace('-','.')+f'-{daily[date]:02d}-'+title_file(item['title'])+'.md')).as_posix()
        revisions=[item] if version==1 else [read_json(p) for p in sorted((metadata/'revisions'/ident).glob('r*.json'))]
        if not revisions or revisions[-1]!=item:raise ValueError('旧当前记录与最新修订不一致，先恢复旧资产：'+ident)
        converted=[];previous=None
        for n,v in enumerate(revisions,1):
            if v['id']!=ident or (version==2 and v['revision']!=n):raise ValueError('旧修订序列损坏：'+ident)
            for keytime in ('created_at','updated_at'):timestamp(v[keytime])
            sections={name:v['sections'][name] for name in SECTIONS}
            if any(not isinstance(x,str) or not x.strip() for x in sections.values()):raise ValueError('旧标准章节缺失')
            if v['status'] not in STATUSES or v['priority'] not in PRIORITIES:raise ValueError('旧确认状态或优先级无效')
            state=LEGACY_PROGRESS.get(v.get('progress','待核实'),v.get('progress','待核实'))
            legacy_unknown=version==1 or v.get('progress')=='待核实'
            source='历史状态待核实' if legacy_unknown else v.get('progress_source','未核实')
            note='旧进度未知；未开始仅作登记占位，需用户确认，不表示已核实从未实施。' if legacy_unknown else v.get('progress_note','待核实')
            data=dict(progress=state,progress_source=source,progress_note=note,completion_evidence=v.get('completion_evidence',[]) if not legacy_unknown else [])
            fields=progress_fields(data,previous,v['updated_at'])
            sids=v.get('source_ids',[])
            for sid in sids:
                if not re.fullmatch(r'SRC-[a-f0-9]{12}',sid):raise ValueError('旧证据ID无效')
                raw=read_json(old/('代码依据' if version==2 else 'sources')/(sid+'.json'))
                if raw['project_id']!=v['project_id']:raise ValueError('旧源码证据归属错误')
                allowed=('id','project_id','path','checked_at','remote_status','deployment_status','local_status','head','branch','working_tree','remote_branch','remote_head','matches_remote','error')
                sources[sid]={k:x for k,x in raw.items() if k in allowed}
            events=[]
            for e in v.get('events',[]):
                timestamp(e['received_at']);events.append({k:e[k] for k in ('id','received_at','time_basis')})
            if not events:events=[dict(id='EV-'+uuid.uuid4().hex[:12],received_at=v['created_at'],time_basis='旧元数据迁移')]
            rec=dict(id=ident,project_id=v['project_id'],title=v['title'],created_at=v['created_at'],updated_at=v['updated_at'],status=v['status'],priority=v['priority'],
                priority_confirmed=v.get('priority_confirmed',False),priority_confirmation=v.get('priority_confirmation',''),revision=n,
                events=events,source_ids=sids,sections=sections,related_ids=v.get('related_ids',[]),reason=v.get('reason','迁移旧需求规范化记录；不读取原话正文。'),path=rel,
                requirement_date=date,archive_week=archive,daily_sequence=daily[date],children=[],legacy_revision=v['revision'],legacy_updated_at=v['updated_at'],**fields)
            for dest in (rel,Path('.reqmgr/records')/(ident+'.json'),Path('修订历史')/ident/f'r{n:04}.md',Path('.reqmgr/revisions')/ident/f'r{n:04}.json'):
                if ws.path(dest).exists():raise ValueError('迁移输出已存在；请选独立空目标资产')
            converted.append(rec);previous=rec
        plans.append(converted)
    # Recheck current metadata so a concurrent old writer cannot silently mix versions.
    if (metadata/'writer.lock').exists() or any(read_json(p)!=v for p,v in zip(paths,current)):
        raise ValueError('旧资产在预览期间变化，请重试')
    return plans,sources

def migrate_legacy(ws,old_root,version,dry_run=False):
    with ws.lock():
        plans,sources=migration_plan(ws,old_root,version)
        preview=[dict(id=versions[-1]['id'],path=versions[-1]['path'],revisions=len(versions)) for versions in plans]
        if not dry_run:
            operation='OP-'+uuid.uuid4().hex[:12]
            journal=dict(id=operation,status='pending',action=f'migrate-v{version}',requirement_ids=[v[-1]['id'] for v in plans],at=now())
            ws.json('.reqmgr/operations/'+operation+'.json',journal)
            for sid,value in sources.items():ws.json('代码依据/'+sid+'.json',value)
            for versions in plans:
                for rec in versions:
                    history=Path('修订历史')/rec['id']/f'r{rec["revision"]:04}.md'
                    ws.write(history,render_req(rec,history))
                    ws.json(Path('.reqmgr/revisions')/rec['id']/f'r{rec["revision"]:04}.json',rec)
                rec=versions[-1];ws.write(rec['path'],render_req(rec,rec['path']));ws.json(Path('.reqmgr/records')/(rec['id']+'.json'),rec)
                ws.audit(dict(action=f'migrate-v{version}',requirement_id=rec['id'],revision=rec['revision'],reason='规范化迁移；旧目录保持不变',progress=rec['progress'],operation=operation))
            rebuild(ws);journal['status']='complete';ws.json('.reqmgr/operations/'+operation+'.json',journal)
    emit(dict(dry_run=dry_run,imported=0 if dry_run else len(plans),requirements=preview,asset_root=str(ws.out),raw_input_read=False,raw_input_saved=False,old_data_changed=False))

def migrate_v1(ws,old_root,dry_run=False):migrate_legacy(ws,old_root,1,dry_run)
def migrate_v2(ws,old_root,dry_run=False):migrate_legacy(ws,old_root,2,dry_run)


def git_read(path, *args):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0')
    # Disable fsmonitor so reading status cannot execute a project-defined monitor.
    r = subprocess.run(['git', '--no-optional-locks', '-c', 'core.fsmonitor=false', '-C', str(path), *args],
                       capture_output=True, text=True, timeout=20, env=env)
    if r.returncode:
        raise ValueError('Git只读查询失败；检查项目路径、Git安装和访问权限')
    return r.stdout.strip()

def discover(ws):
    found = []
    for root in ws.roots:
        for path in sorted(root.iterdir()):
            if path.name.startswith('.') or path.is_symlink() or not path.is_dir():
                continue
            markers = [n for n in ('.git', 'README.md', 'package.json', 'pyproject.toml', 'pom.xml', 'compose.yaml') if (path/n).exists()]
            found.append(dict(name=path.name, path=str(path), markers=markers,
                              registered_ids=[k for k,v in ws.projects.items() if Path(v['path']) == path]))
    with ws.lock():
        ws.json('.reqmgr/catalog.json', dict(checked_at=now(), projects=found))
    emit(found)

def snapshot(ws, key, remote=False):
    if key not in ws.projects:
        raise ValueError('项目未登记')
    p = Path(ws.projects[key]['path'])
    result = dict(project_id=key, path=str(p), checked_at=now(), remote_status='未核查', deployment_status='未知')
    if not p.is_dir():
        result['local_status'] = '目录不存在'
    else:
        try:
            if Path(git_read(p, 'rev-parse', '--show-toplevel')).resolve() != p.resolve():
                raise ValueError('登记目录不是独立Git仓库根目录；不能把父仓库版本当作本项目版本')
            result.update(head=git_read(p, 'rev-parse', 'HEAD'), branch=git_read(p, 'branch', '--show-current'),
                          working_tree=git_read(p, 'status', '--porcelain=v1', '--untracked-files=normal'), local_status='已核查')
            branch = ws.projects[key].get('target_branch') or result['branch']
            if remote:
                if not re.fullmatch(r'[A-Za-z0-9._/-]+', branch or '') or '..' in branch or branch.startswith('-'):
                    raise ValueError('远程查询需要有效目标分支')
                target = git_read(p, 'ls-remote', '--exit-code', 'origin', 'refs/heads/' + branch)
                sha = target.split()[0]
                result.update(remote_status='已核查', remote_branch=branch, remote_head=sha,
                              matches_remote=(sha == result['head']))
        except (ValueError, FileNotFoundError, subprocess.TimeoutExpired) as e:
            result['error'] = str(e)
            result.setdefault('local_status', '无法核查')
            if remote and result['remote_status'] != '已核查':
                result['remote_status'] = '无法核查'
    ident = 'SRC-' + uuid.uuid4().hex[:12]
    result['id'] = ident
    with ws.lock():
        ws.json(Path('代码依据')/(ident+'.json'), result)
    emit(result)

def register(ws,items):
    if not isinstance(items,list):raise ValueError('项目登记必须是数组')
    current={r['project_id'] for r in ws.records()}-{'unassigned'}
    new={};allowed=('id','name','path','aliases','keywords','target_branch','notes')
    for i in items:
        reject_unknown(i,allowed,'项目登记');key=safe_id(i['id']);p=asset_path(i['path'])
        if key=='unassigned' or key in new or not any(p.is_relative_to(r) for r in ws.roots):raise ValueError('项目ID重复、保留或路径越界')
        new[key]=dict(i,path=str(p))
    if not current.issubset(new):raise ValueError('不能移除已经关联需求的项目ID')
    with ws.lock():
        ws.cfg['projects']=list(new.values());ws.projects=new
        ws.json('.reqmgr/config.json',ws.cfg);rebuild(ws)
    info(ws)

def main():
    parser=argparse.ArgumentParser(description='需求管理v3：不保存原话；资产独立；严禁业务代码改造')
    parser.add_argument('--asset-root',default=os.environ.get('REQMGR_ASSET_ROOT'),required=not os.environ.get('REQMGR_ASSET_ROOT'),help='事先明确的固定需求资产绝对路径，可由REQMGR_ASSET_ROOT提供')
    sub=parser.add_subparsers(dest='command',required=True)
    s=sub.add_parser('init');s.add_argument('--source-root',action='append',required=True);s.add_argument('--projects-file')
    sub.add_parser('info');sub.add_parser('discover');sub.add_parser('reindex');sub.add_parser('validate')
    s=sub.add_parser('register');g=s.add_mutually_exclusive_group(required=True);g.add_argument('--file');g.add_argument('--stdin',action='store_true')
    s=sub.add_parser('snapshot');s.add_argument('project_id');s.add_argument('--remote',action='store_true')
    s=sub.add_parser('apply');g=s.add_mutually_exclusive_group(required=True);g.add_argument('--bundle');g.add_argument('--stdin',action='store_true')
    s=sub.add_parser('weekly');s.add_argument('--week',help='该周任意日期，例如2026-10-09')
    s=sub.add_parser('migrate-v1');s.add_argument('--v1-data',required=True);s.add_argument('--dry-run',action='store_true')
    s=sub.add_parser('migrate-v2');s.add_argument('--v2-assets',required=True);s.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    if args.command=='init':init_asset(args.asset_root,args.source_root,args.projects_file);return
    ws=Workspace(args.asset_root)
    if args.command=='info':info(ws)
    elif args.command=='discover':discover(ws)
    elif args.command=='snapshot':snapshot(ws,args.project_id,args.remote)
    elif args.command=='register':register(ws,json.load(sys.stdin) if args.stdin else read_json(args.file))
    elif args.command=='apply':apply_bundle(ws,json.load(sys.stdin) if args.stdin else read_json(args.bundle))
    elif args.command=='migrate-v1':migrate_v1(ws,args.v1_data,args.dry_run)
    elif args.command=='migrate-v2':migrate_v2(ws,args.v2_assets,args.dry_run)
    elif args.command=='validate':validate(ws)
    else:
        with ws.lock():rebuild(ws,args.week if args.command=='weekly' else None)
        info(ws)

if __name__=='__main__':
    try:main()
    except (ValueError,KeyError,OSError,json.JSONDecodeError) as e:
        print('ERROR: '+str(e),file=sys.stderr);sys.exit(1)

