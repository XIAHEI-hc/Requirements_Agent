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
PROGRESS = ('未开始', '未完成', '已完成', '待核实')
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
    t = timestamp(value)
    monday = t.date() - dt.timedelta(days=t.weekday())
    sunday = monday + dt.timedelta(days=6)
    return Path(monday.strftime('%Y-%m')) / f'{monday}_至_{sunday}' / t.strftime('%Y-%m-%d')

def safe_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', value):
        raise ValueError('ID 仅允许小写英文、数字和短横线，最长64字符')
    return value

def title_file(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value).strip(' .')[:60]
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
        if self.cfg.get('version')!=2 or self.cfg.get('timezone')!='Asia/Shanghai':
            raise ValueError('需要v2配置和Asia/Shanghai时区')
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
        return [read_json(self.path(p.relative_to(self.out))) for p in sorted(self.path('.reqmgr/records').glob('REQ-*.json'))]
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
    cfg=dict(version=2,timezone='Asia/Shanghai',asset_id='ASSET-'+uuid.uuid4().hex[:12],asset_root=str(out),source_roots=[str(s) for s in sources],projects=items)
    # No fallback directory exists: explicit root is required.
    atomic(out/'.reqmgr/config.json',json.dumps(cfg,ensure_ascii=False,indent=2)+'\n')
    ws=Workspace(out)
    with ws.lock():
        ws.write('资产说明.md', '# 固定需求管理资产\n\n资产ID：'+cfg['asset_id']+'\n\n固定目录：`'+str(out)+'`\n\n源码根目录：'+', '.join(str(p) for p in sources)+'\n\nAgent包可以单独升级，长期资产不放入包内。日常从需求总览.md和周总汇/索引.md查看；不保存原话。\n')
        rebuild(ws)
    info(ws)

def info(ws):
    emit(dict(agent_package=str(BASE),asset_root=str(ws.out),asset_id=ws.cfg['asset_id'],source_roots=[str(p) for p in ws.roots],
              configuration=str(ws.config_path),dashboard=str(ws.path('需求总览.md')),weekly_index=str(ws.path('周总汇/索引.md')),
              raw_input_saved=False,prohibition='严禁业务代码改造'))

def reject_unknown(obj,allowed,label):
    if not isinstance(obj,dict) or set(obj)-set(allowed):raise ValueError(label+'含未支持字段；禁止raw_input/original_text/原话等存档字段')

def progress_badge(value):
    icon,color={'未开始':('🔵','#2563eb'),'未完成':('🟠','#d97706'),'已完成':('🟢','#15803d'),'待核实':('⚪','#64748b')}[value]
    return f'<span style="color:{color}">{icon} {value}</span>'

def md_cell(value):
    text=html.escape(str(value)).replace('|','／').replace('\n',' ').replace('\r',' ')
    return text.replace('[','\\[').replace(']','\\]')

def render_req(rec,rel):
    def link(target):return os.path.relpath(target,Path(rel).parent).replace(os.sep,'/')
    lines=[f'# {rec["id"]} {rec["title"]}','','| 字段 | 内容 |','|---|---|']
    for name,key in [('项目','project_id'),('首次提出','created_at'),('最近整理','updated_at'),('需求状态','status'),('建议优先级','priority'),('修订','revision')]:
        lines.append(f'| {name} | {md_cell(rec[key])} |')
    lines += [f'| 实施进度 | {progress_badge(rec["progress"])} |','', '**仅管理需求与进度记录；严禁本Agent修改业务代码、配置、数据库或部署。**','',
              '## 时间与记录依据','', '不保存用户原话。只记录消息时间、整理后的需求、变更原因和状态依据。']
    for e in rec['events']:lines.append(f'- {e["id"]} · {e["received_at"]} · {e["time_basis"]}')
    lines += ['','## 进度依据','',f'来源：{rec["progress_source"]}\n\n说明：{rec["progress_note"]}', '', '完成证据：']
    lines += ['- '+x for x in rec['completion_evidence']] or ['- 无；不得自动宣布已完成。']
    lines += ['','## 排序确认','',rec['priority_confirmation'] if rec['priority_confirmed'] else '当前仅为建议排序，未获用户确认。','', '## 代码核查依据','']
    lines += [f'- [{sid}]({link("代码依据/"+sid+".json")})' for sid in rec['source_ids']] or ['未提供源码核查证据；本地/远程/部署状态不据此认定。']
    for name in SECTIONS:lines += ['', '## '+name,'',rec['sections'][name]]
    lines += ['','## 关联需求','',', '.join(rec['related_ids']) or '无','','## 本次变更原因','',rec['reason'],'']
    return '\n'.join(lines)

def table(items,base,priority=False):
    order={x:i for i,x in enumerate(PRIORITIES)}
    items=sorted(items,key=(lambda r:(r['status'] in ('已撤回','已合并'),order[r['priority']],r['created_at'],r['id'])) if priority else (lambda r:(r['created_at'],r['id'])))
    lines=['| 需求 | 项目 | 提出时间 | 需求状态 | 实施进度 | 优先级 | 进度依据 |','|---|---|---|---|---|---|---|']
    for r in items:
        href=os.path.relpath(r['path'],base).replace(os.sep,'/')
        lines.append(f'| [{r["id"]} {md_cell(r["title"])}]({href}) | {md_cell(r["project_id"])} | {r["created_at"][:10]} | {r["status"]} | {progress_badge(r["progress"])} | {r["priority"]} | {md_cell(r["progress_source"])}：{md_cell(r["progress_note"])} |')
    return '\n'.join(lines)+'\n' if items else '本项暂无需求。\n'

def week_bounds(date):
    d=dt.date.fromisoformat(date[:10]);start=d-dt.timedelta(days=d.weekday())
    return dt.datetime.combine(start,dt.time(),TZ),dt.datetime.combine(start+dt.timedelta(days=7),dt.time(),TZ)

def week_folder(date):
    a,b=week_bounds(date)
    return Path(a.strftime('%Y-%m'))/f'{a.date()}_至_{(b-dt.timedelta(days=1)).date()}'

def active(rec):return rec['status'] not in ('已撤回','已合并')

def week_state(ws,date,project=None):
    start,end=week_bounds(date);cutoff=min(timestamp(now()),end-dt.timedelta(microseconds=1))
    snapshots=[];new=[];changed=[];carry=[];completed=[]
    for current in ws.records():
        if project and current['project_id']!=project:continue
        if timestamp(current['created_at'])>cutoff:continue
        revisions=ws.revisions(current['id'])
        before=[r for r in revisions if timestamp(r['updated_at'])<=cutoff]
        if before:r=before[-1]
        else:
            r=dict(current,progress='待核实',progress_source='历史状态未知',progress_note='此需求晚于该周录入，无法重建当周状态',status='待澄清')
        snapshots.append(r)
        if start<=timestamp(r['created_at'])<end:new.append(r)
        if any(start<=timestamp(v['updated_at'])<=cutoff for v in revisions):changed.append(r)
        if timestamp(r['created_at'])<start and active(r) and r['progress']!='已完成':carry.append(r)
        previous=None
        transitions=[]
        for v in before:
            if v['progress']=='已完成' and (previous is None or previous['progress']!='已完成'):
                transitions.append(timestamp(v['updated_at']))
            previous=v
        if r['progress']=='已完成' and any(start<=t<=cutoff for t in transitions):completed.append(r)
    related={r['id']:r for r in new+changed+carry+completed}
    return dict(start=start,end=end,cutoff=cutoff,all=snapshots,new=new,changed=changed,carry=carry,completed=completed,related=list(related.values()))

def weekly_report(ws,date,project=None):
    st=week_state(ws,date,project)
    prefix=Path('周总汇') if not project else (Path('待归属')/'周总汇' if project=='unassigned' else Path('项目')/project/'周总汇')
    rel=prefix/week_folder(date)/'每周需求总汇.md';base=rel.parent
    enddate=(st['end']-dt.timedelta(days=1)).date()
    lines=[f'# {project or "全部项目"} 每周需求总汇 · {st["start"].date()} 至 {enddate}','',f'资产目录：`{ws.out}`', '',
           f'状态截点：{st["cutoff"].isoformat(timespec="seconds")}；生成时间：{now()}。', '',
           '历史周使用截点前已记录的修订；当前周显示当前状态。晚录入且无法重建历史的需求标记待核实。表格链接打开需求当前详情，历史进度以本表截点为准。', '',
           '进度图例：'+ ' / '.join(progress_badge(x) for x in PROGRESS), '', '## 本周概况', '',
           '| 本周新增 | 本周更新（含新增） | 历史遗留未完成 | 本周完成 | 本周涉及需求（去重） |','|---|---|---|---|---|',
           f'| {len(st["new"])} | {len(st["changed"])} | {len(st["carry"])} | {len(st["completed"])} | {len(st["related"])} |','',
           '完成数按进度转为已完成计数，纯文档修订不重复计完成；用户报告与只读证据的来源单独可见。需求确认不等于实施完成。', '', '## 本周涉及需求的项目统计','',
           '| 项目 | 未开始 | 未完成 | 已完成 | 待核实 | 合计 |','|---|---|---|---|---|---|']
    for key in sorted({r['project_id'] for r in st['related']}):
        group=[r for r in st['related'] if r['project_id']==key]
        counts=[sum(r['progress']==x for r in group) for x in PROGRESS]
        lines.append('| '+key+' | '+' | '.join(map(str,counts))+f' | {len(group)} |')
    if not st['related']:lines.append('| 无 | 0 | 0 | 0 | 0 | 0 |')
    for label,key in [('本周需求总表','related'),('本周新增','new'),('本周更新','changed'),('历史遗留未开始/未完成','carry'),('本周已完成','completed')]:
        lines+=['','## '+label,'',table(st[key],base,True)]
    pending=[r for r in st['related'] if r['project_id']=='unassigned' or r['status']=='待澄清' or r['progress']=='待核实']
    lines+=['','## 待归属与待澄清','',table(pending,base,True), '', '本周没有新需求时仍显示历史遗留。可以运行weekly命令明确生成任意周，或每次归档/进度更新自动刷新。没有安装定时任务。','']
    ws.write(rel,'\n'.join(lines));return rel

def rebuild(ws,extra_week=None):
    records=ws.records();weeks={week_bounds(now())[0].date().isoformat()}
    if extra_week:weeks.add(week_bounds(extra_week)[0].date().isoformat())
    for r in records:
        weeks.add(week_bounds(r['created_at'])[0].date().isoformat())
        weeks.update(week_bounds(v['updated_at'])[0].date().isoformat() for v in ws.revisions(r['id']))
    # Keep explicit empty-week reports visible after future rebuilds.
    for p in ws.path('周总汇').glob('*/*/每周需求总汇.md'):
        date=p.parent.name.split('_至_')[0]
        weeks.add(week_bounds(date)[0].date().isoformat())
    legend='进度图例：'+' / '.join(progress_badge(x) for x in PROGRESS)
    ws.write('需求总览.md','# 需求总览\n\n固定资产目录：`'+str(ws.out)+'`\n\n[每周需求总汇索引](周总汇/索引.md)\n\n'+legend+'\n\n'+table(records,Path('.')))
    keys=sorted(set(ws.projects)|{r['project_id'] for r in records})
    for key in keys:
        base=Path('待归属') if key=='unassigned' else Path('项目')/key
        ws.write(base/'需求索引.md',f'# {key} 需求索引\n\n'+legend+'\n\n'+table([r for r in records if r['project_id']==key],base,True))
    links=[]
    for date in sorted(weeks,reverse=True):
        path=weekly_report(ws,date);href=path.relative_to('周总汇').as_posix()
        links.append(f'- [{path.parent.name}]({href})')
        for key in keys:weekly_report(ws,date,key)
    ws.write('周总汇/索引.md','# 每周需求总汇索引\n\n每周包含新增、更新、完成及以前遗留项。\n\n'+'\n'.join(links)+'\n')

def prepare(ws,bundle):
    reject_unknown(bundle,('received_at','time_basis','requirements'),'批次')
    received=timestamp(bundle.get('received_at') or now()).isoformat(timespec='seconds')
    basis=bundle.get('time_basis') or ('用户提供' if bundle.get('received_at') else '系统接收时间')
    if basis not in TIME_BASES or not bundle.get('received_at') and basis!='系统接收时间':raise ValueError('无效时间依据')
    items=bundle['requirements']
    if not isinstance(items,list) or not items:raise ValueError('requirements必须是非空数组')
    old={r['id']:r for r in ws.records()};seen=set();prepared=[]
    event=dict(id='EV-'+uuid.uuid4().hex[:12],received_at=received,time_basis=basis)
    allowed=('id','expected_revision','project_id','title','status','priority','priority_confirmed','priority_confirmation','source_ids','related_ids','reason','sections','progress','progress_source','progress_note','completion_evidence')
    for item in items:
        reject_unknown(item,allowed,'需求')
        key=item.get('project_id','unassigned')
        if key!='unassigned' and key not in ws.projects:raise ValueError('项目未登记')
        title=item['title'].strip()
        if not title or len(title)>200 or '\n' in title or '\r' in title:raise ValueError('需求标题需为200字符内单行文字')
        ident=item.get('id');previous=None
        if ident:
            if ident not in old or item.get('expected_revision')!=old[ident]['revision']:raise ValueError('修订ID不存在或expected_revision冲突')
            previous=old[ident]
            if key!=previous['project_id']:raise ValueError('转交归属请新建正确项目条目并关联保留旧项')
        else:ident='REQ-'+key.upper()+'-'+uuid.uuid4().hex[:10].upper()
        if ident in seen:raise ValueError('同批次重复修订')
        seen.add(ident)
        status=item.get('status',(previous or {}).get('status','待澄清'));priority=item.get('priority',(previous or {}).get('priority','待评估'))
        if status not in STATUSES or priority not in PRIORITIES:raise ValueError('无效需求状态或优先级')
        reason=item.get('reason','').strip()
        if not reason:raise ValueError('需要整理后的新增/修订原因，不要复制原话')
        sections=item['sections'];reject_unknown(sections,SECTIONS,'需求章节')
        if any(not isinstance(sections.get(x),str) or not sections[x].strip() for x in SECTIONS):raise ValueError('统一需求章节必填；未知写待核实')
        progress=item.get('progress',(previous or {}).get('progress','未开始'))
        progress_source=item.get('progress_source',(previous or {}).get('progress_source','新需求初始化'))
        progress_note=item.get('progress_note',(previous or {}).get('progress_note','新录入需求默认未开始；不是代码状态核查结论'))
        evidence=item.get('completion_evidence',(previous or {}).get('completion_evidence',[]))
        if progress not in PROGRESS or not isinstance(progress_source,str) or not progress_source.strip() or not isinstance(progress_note,str) or not progress_note.strip():raise ValueError('实施进度与依据必填')
        if previous and progress!=previous['progress'] and not all(k in item for k in ('progress_source','progress_note')):raise ValueError('改变实施进度需显式更新进度来源和说明')
        if progress!='未开始' and progress_source=='新需求初始化':raise ValueError('初始化依据只能用于未开始，其它进度必须提供实际依据')
        if not isinstance(evidence,list) or any(not isinstance(x,str) or not x.strip() for x in evidence):raise ValueError('完成证据应为摘要/引用数组，不能保存聊天原话')
        if progress=='已完成' and (not evidence or progress_source in ('新需求初始化','未核实')):raise ValueError('已完成必须有用户报告或只读证据及完成依据；不得从提交/确认自动推断')
        sids=item.get('source_ids',(previous or {}).get('source_ids',[]))
        if not isinstance(sids,list):raise ValueError('source_ids须为数组')
        for sid in sids:
            if not isinstance(sid,str) or not re.fullmatch(r'SRC-[a-f0-9]{12}',sid):raise ValueError('无效源码证据ID')
            s=read_json(ws.path('代码依据/'+sid+'.json'))
            if s['project_id']!=key:raise ValueError('禁止跨项目引用源码现状证据')
        related=item.get('related_ids',(previous or {}).get('related_ids',[]))
        if not isinstance(related,list) or any(x not in old for x in related):raise ValueError('关联需为真实已存在需求ID')
        confirmed=item.get('priority_confirmed',(previous or {}).get('priority_confirmed',False))
        confirmation=item.get('priority_confirmation',(previous or {}).get('priority_confirmation',''))
        if not isinstance(confirmed,bool) or confirmed and not confirmation:raise ValueError('用户确认排序需说明确认依据')
        if previous and priority!=previous['priority'] and 'priority_confirmed' not in item:confirmed=False;confirmation=''
        created=(previous or {}).get('created_at',received)
        rel=(previous or {}).get('path')
        if not rel:
            prefix=Path('待归属') if key=='unassigned' else Path('项目')/key/'需求'
            rel=(prefix/bucket(created)/(timestamp(created).strftime('%Y-%m-%d_%H%M%S')+'_'+ident+'_'+title_file(title)+'.md')).as_posix()
        rec=dict(id=ident,project_id=key,title=title,created_at=created,updated_at=now(),status=status,priority=priority,priority_confirmed=confirmed,priority_confirmation=confirmation,
                 revision=(previous or {}).get('revision',0)+1,events=(previous or {}).get('events',[])+[event],source_ids=sids,sections=sections,related_ids=related,
                 reason=reason,path=rel,progress=progress,progress_source=progress_source,progress_note=progress_note,completion_evidence=evidence)
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
        if r['progress']=='已完成' and not r['completion_evidence']:errors.append('完成缺少依据：'+r['id'])
    for p in ws.path('.reqmgr/operations').glob('*.json'):
        if read_json(ws.path(p.relative_to(ws.out)))['status']!='complete':errors.append('存在中断批次：'+p.name)
    emit(dict(ok=not errors,requirements=len(ws.records()),asset_root=str(ws.out),raw_input_saved=False,errors=errors))
    if errors:raise ValueError('校验未通过')

def migrate_v1(ws,old_root):
    old=asset_path(old_root)
    if old==ws.out or old.is_relative_to(ws.out) or ws.out.is_relative_to(old):raise ValueError('新旧资产目录必须彼此分离')
    if ws.records():raise ValueError('迁移目标已有需求；v1导入只允许新初始化的空资产')
    current=[read_json(p) for p in sorted((old/'records').glob('REQ-*.json'))]
    if not current:raise ValueError('v1目录无records，禁止猜测导入对象')
    transformed=[];sources={}
    for item in current:
        key=item['project_id'];ident=item['id']
        if key!='unassigned' and key not in ws.projects:raise ValueError('先登记v1中使用的项目ID：'+key)
        if not re.fullmatch(r'REQ-[A-Z0-9-]+',ident):raise ValueError('v1需求ID无效')
        timestamp(item['created_at'])
        if item['status'] not in STATUSES or item['priority'] not in PRIORITIES:raise ValueError('v1需求状态或优先级无效')
        sections={name:item['sections'][name] for name in SECTIONS}
        if any(not isinstance(v,str) or not v.strip() for v in sections.values()):raise ValueError('v1标准章节缺失')
        for sid in item.get('source_ids',[]):
            if not re.fullmatch(r'SRC-[a-f0-9]{12}',sid):raise ValueError('v1证据ID无效')
            p=old/'sources'/(sid+'.json')
            if not p.is_file():raise ValueError('v1源码证据缺失')
            value=read_json(p)
            if value['project_id']!=key:raise ValueError('v1源码证据归属错误')
            allowed=('id','project_id','path','checked_at','remote_status','deployment_status','local_status','head','branch','working_tree','remote_branch','remote_head','matches_remote','error')
            sources[sid]={k:v for k,v in value.items() if k in allowed}
        prefix=Path('待归属') if key=='unassigned' else Path('项目')/key/'需求'
        rel=(prefix/bucket(item['created_at'])/(timestamp(item['created_at']).strftime('%Y-%m-%d_%H%M%S')+'_'+ident+'_'+title_file(item['title'])+'.md')).as_posix()
        rec=dict(id=ident,project_id=key,title=item['title'],created_at=item['created_at'],updated_at=now(),status=item['status'],priority=item['priority'],
                 priority_confirmed=item['priority_confirmed'],priority_confirmation=item.get('priority_confirmation',''),revision=1,
                 events=[dict(id='EV-'+uuid.uuid4().hex[:12],received_at=item['created_at'],time_basis='v1元数据迁移')],source_ids=item.get('source_ids',[]),sections=sections,related_ids=item.get('related_ids',[]),
                 reason='迁移v1当前需求；不读取、不复制原话正文；旧历史留在原v1资产中。',path=rel,progress='待核实',progress_source='v1无实施进度字段',progress_note='旧需求状态不能推断实施进度，需用户报告或只读证据补充',completion_evidence=[],legacy_revision=item['revision'],legacy_updated_at=item['updated_at'])
        transformed.append(rec)
    with ws.lock():
        for sid,value in sources.items():ws.json('代码依据/'+sid+'.json',value)
        commit_records(ws,transformed,'migrate-v1')
    emit(dict(imported=len(transformed),asset_root=str(ws.out),raw_input_read=False,raw_input_saved=False,old_data_changed=False))


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
    parser=argparse.ArgumentParser(description='需求管理v2：不保存原话；资产独立；严禁业务代码改造')
    parser.add_argument('--asset-root',default=os.environ.get('REQMGR_ASSET_ROOT'),required=not os.environ.get('REQMGR_ASSET_ROOT'),help='事先明确的固定需求资产绝对路径，可由REQMGR_ASSET_ROOT提供')
    sub=parser.add_subparsers(dest='command',required=True)
    s=sub.add_parser('init');s.add_argument('--source-root',action='append',required=True);s.add_argument('--projects-file')
    sub.add_parser('info');sub.add_parser('discover');sub.add_parser('reindex');sub.add_parser('validate')
    s=sub.add_parser('register');g=s.add_mutually_exclusive_group(required=True);g.add_argument('--file');g.add_argument('--stdin',action='store_true')
    s=sub.add_parser('snapshot');s.add_argument('project_id');s.add_argument('--remote',action='store_true')
    s=sub.add_parser('apply');g=s.add_mutually_exclusive_group(required=True);g.add_argument('--bundle');g.add_argument('--stdin',action='store_true')
    s=sub.add_parser('weekly');s.add_argument('--week',help='该周任意日期，例如2026-10-09')
    s=sub.add_parser('migrate-v1');s.add_argument('--v1-data',required=True)
    args=parser.parse_args()
    if args.command=='init':init_asset(args.asset_root,args.source_root,args.projects_file);return
    ws=Workspace(args.asset_root)
    if args.command=='info':info(ws)
    elif args.command=='discover':discover(ws)
    elif args.command=='snapshot':snapshot(ws,args.project_id,args.remote)
    elif args.command=='register':register(ws,json.load(sys.stdin) if args.stdin else read_json(args.file))
    elif args.command=='apply':apply_bundle(ws,json.load(sys.stdin) if args.stdin else read_json(args.bundle))
    elif args.command=='migrate-v1':migrate_v1(ws,args.v1_data)
    elif args.command=='validate':validate(ws)
    else:
        with ws.lock():rebuild(ws,args.week if args.command=='weekly' else None)
        info(ws)

if __name__=='__main__':
    try:main()
    except (ValueError,KeyError,OSError,json.JSONDecodeError) as e:
        print('ERROR: '+str(e),file=sys.stderr);sys.exit(1)

