import sys
sys.dont_write_bytecode=True
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

BASE=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('reqmgr',BASE/'scripts/reqmgr.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class V3Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.home=Path(self.temp.name)
        self.src=self.home/'sources';self.src.mkdir();self.asset=self.home/'assets'
        self.projects=[]
        for key in ('alpha','beta'):
            p=self.src/key;p.mkdir();(p/'README.md').write_text('fictional '+key,encoding='utf-8')
            self.projects.append(dict(id=key,name=key,path=str(p)))
        self.projectfile=self.home/'projects.json';self.projectfile.write_text(json.dumps(self.projects),encoding='utf-8')
        self.clock='2026-10-09T23:32:14+08:00';self.original_now=m.now;m.now=lambda:self.clock
        self.kit_before=self.digest(BASE)
        with contextlib.redirect_stdout(io.StringIO()):m.init_asset(self.asset,[self.src],self.projectfile)
        self.ws=m.Workspace(self.asset)
        self.example=m.read_json(BASE/'templates/bundle.example.json')['requirements'][0]

    def tearDown(self):m.now=self.original_now;self.temp.cleanup()
    def digest(self,root):return {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    def item(self,key='alpha',title=None):
        r=copy.deepcopy(self.example);r['project_id']=key
        if title:r['title']=title
        return r
    def apply(self,items,received=None):
        b=dict(received_at=received or self.clock,time_basis='平台消息时间',requirements=items)
        with contextlib.redirect_stdout(io.StringIO()):m.apply_bundle(self.ws,b)
    def update(self,record,**changes):
        item=self.item(record['project_id'],record['title'])
        item.update(id=record['id'],expected_revision=record['revision'],reason='规范化进度更新',progress=record['progress'],progress_source=record['progress_source'],progress_note=record['progress_note'],completion_evidence=record['completion_evidence'])
        item.update(changes);self.apply([item])
        return next(r for r in self.ws.records() if r['id']==record['id'])
    def validate(self):
        with contextlib.redirect_stdout(io.StringIO()):m.validate(self.ws)

    def test_independent_assets_and_no_raw_fields(self):
        before=self.digest(self.src)
        self.apply([self.item(),self.item('beta'),self.item('unassigned')]);self.validate()
        self.assertEqual(before,self.digest(self.src));self.assertEqual(self.kit_before,self.digest(BASE))
        for forbidden in ('原始输入','intakes','staging','data'):
            self.assertFalse((self.asset/forbidden).exists())
        bad=dict(received_at=self.clock,requirements=[self.item()],raw_input='RAW_SENTINEL_NOT_TO_BE_STORED')
        with self.assertRaises(ValueError):m.apply_bundle(self.ws,bad)
        self.assertFalse(any(b'RAW_SENTINEL' in p.read_bytes() for p in self.asset.rglob('*') if p.is_file()))

    def test_missing_explicit_root_cli(self):
        env=dict(__import__('os').environ);env.pop('REQMGR_ASSET_ROOT',None)
        r=subprocess.run([sys.executable,'-B',str(BASE/'scripts/reqmgr.py'),'info'],env=env,capture_output=True,text=True)
        self.assertNotEqual(r.returncode,0);self.assertIn('--asset-root',r.stderr)

    def test_init_and_info_announce_fixed_paths(self):
        result=io.StringIO()
        with contextlib.redirect_stdout(result):m.info(self.ws)
        data=json.loads(result.getvalue())
        self.assertEqual(data['asset_root'],str(self.asset));self.assertEqual(data['agent_package'],str(BASE));self.assertFalse(data['raw_input_saved'])
        with self.assertRaises(ValueError):m.init_asset(self.asset,[self.src],self.projectfile)

    def test_relative_and_package_path_rejected(self):
        with self.assertRaises(ValueError):m.init_asset('relative-assets',[self.src])
        with self.assertRaises(ValueError):m.init_asset(BASE/'data',[self.src])
        with self.assertRaises(ValueError):m.init_asset(BASE.parent,[self.src])

    def test_source_overlap_traversal_symlink(self):
        with self.assertRaises(ValueError):m.init_asset(self.src/'alpha'/'docs',[self.src])
        with self.assertRaises(ValueError):self.ws.write('../outside.md','x')
        with self.assertRaises(ValueError):self.ws.write(str(self.src/'alpha'/'changed.md'),'x')
        p=self.asset/'escape'
        try:p.symlink_to(self.src,target_is_directory=True)
        except OSError:self.skipTest('symlinks unavailable')
        with self.assertRaises(ValueError):self.ws.write('escape/changed.md','x')

    def test_calendar_weekend_year_timezone(self):
        self.assertEqual(m.bucket('2026-11-01T09:00:00+08:00').as_posix(),'10月/10.26-10.30')
        self.assertEqual(m.bucket('2027-01-01T09:00:00+08:00').as_posix(),'12月/12.28-01.01')
        self.assertEqual(m.bucket('2026-10-09T18:00:00+00:00').as_posix(),'10月/10.05-10.09')
        self.assertEqual(m.archive_folder(self.ws,'2027-10-04').as_posix(),'2027年/10月/10.04-10.08')
        with self.assertRaises(ValueError):m.timestamp('2026-10-09T23:00:00')

    def test_requirement_confirmation_is_not_completion(self):
        item=self.item();item['status']='已确认';self.apply([item])
        self.assertEqual(self.ws.records()[0]['progress'],'未开始')
        bad=self.item();bad['progress']='已解决'
        with self.assertRaises(ValueError):self.apply([bad])

    def test_five_states_and_resolution_basis(self):
        items=[]
        for state in m.PROGRESS:
            item=self.item(title=state+'演示')
            item.update(progress=state,progress_source='用户报告',progress_note='用户反馈规范化摘要，未独立验证')
            if state=='已解决':item['completion_evidence']=['用户反馈目标行为已核对']
            items.append(item)
        self.apply(items);self.validate()
        text=self.ws.path('需求总览.md').read_text(encoding='utf-8')
        for icon in ('⚪ 未开始','🟡 进行中','🔴 已阻塞','🟢 已解决','⚫ 已取消'):self.assertIn(icon,text)
        self.assertNotIn('<span',text)
        report=self.ws.path('10月/10.05-10.09/本周需求总汇.md').read_text(encoding='utf-8')
        self.assertIn('| 5 | 0 | 1 | 1 | 1 | 1 | 1 |',report)

    def test_old_backlog_not_counted_as_current_week_new(self):
        self.clock='2026-10-02T10:00:00+08:00';self.apply([self.item()]);r=self.ws.records()[0]
        self.clock='2026-10-09T23:32:14+08:00'
        with self.ws.lock():m.rebuild(self.ws)
        state=m.week_state(self.ws,self.clock)
        self.assertEqual(len(state['new']),0);self.assertEqual(len(state['cross']),0)
        report=self.ws.path('10月/10.05-10.09/本周需求总汇.md').read_text(encoding='utf-8')
        self.assertIn('| 0 | 0 | 0 | 0 | 0 | 0 | 0 |',report)
        self.assertIn(r['id'],self.ws.path('需求总览.md').read_text(encoding='utf-8'))

    def test_original_week_latest_state_and_cross_week_events(self):
        self.clock='2026-09-28T21:20:29+08:00';self.apply([self.item()]);r=self.ws.records()[0]
        self.clock='2026-10-08T09:26:34+08:00'
        r=self.update(r,progress='已解决',progress_source='用户报告',progress_note='用户反馈全部完成，未独立验证',completion_evidence=['用户反馈验收已完成'])
        self.assertEqual(m.week_state(self.ws,'2026-09-28')['new'][0]['progress'],'已解决')
        self.assertEqual(len(m.week_state(self.ws,'2026-10-08')['new']),0)
        self.assertEqual(len(m.week_state(self.ws,'2026-10-08')['cross']),1)
        original=self.ws.path('09月/09.28-10.02/本周需求总汇.md').read_text(encoding='utf-8')
        self.assertIn('# 09.28-10.02 本周需求总汇',original)
        self.assertIn('2026-10-08 09:26:34 +08:00',original)
        self.assertIn('| 1 | 0 | 0 | 0 | 0 | 1 | 0 |',original)
        current=self.ws.path('10月/10.05-10.09/本周需求总汇.md').read_text(encoding='utf-8')
        self.assertIn('../../09月/09.28-10.02/',current)
        self.clock='2026-10-16T10:00:00+08:00';r=self.update(r,title='仅更改标题')
        self.assertEqual(r['resolved_at'],'2026-10-08T09:26:34+08:00')
        self.assertEqual(len(m.week_state(self.ws,'2026-10-08')['cross']),1)

    def test_early_filing_explicit_week_and_real_creation_time(self):
        self.clock='2026-09-28T22:00:00+08:00'
        item=self.item();item.update(requirement_date='2026-10-09',archive_week='2026-10-05')
        self.apply([item]);r=self.ws.records()[0]
        self.assertEqual(r['id'],'20261009-01');self.assertEqual(r['created_at'],self.clock)
        self.assertEqual(r['path'],'10月/10.05-10.09/10.09-01-支持原图双图对比.md')
        self.assertEqual(len(m.week_state(self.ws,'2026-09-28')['new']),0)
        self.assertEqual(len(m.week_state(self.ws,'2026-10-09')['new']),1)
        self.clock='2026-10-09T11:00:00+08:00'
        r=self.update(r,title='修订标题')
        self.assertEqual(r['archive_week'],'2026-10-05');self.assertEqual(r['created_at'],'2026-09-28T22:00:00+08:00')

    def test_revision_conflict_and_immutable_history(self):
        self.apply([self.item()]);r=self.ws.records()[0]
        history=self.ws.path(f'修订历史/{r["id"]}/r0001.md').read_bytes()
        new=self.update(r,title='新增条件')
        self.assertEqual(new['path'],r['path']);self.assertEqual(new['created_at'],r['created_at'])
        self.assertEqual(history,self.ws.path(f'修订历史/{r["id"]}/r0001.md').read_bytes())
        with self.assertRaises(ValueError):self.update(r,title='冲突更新')

    def test_batch_preflight_no_partial_write(self):
        with self.assertRaises(ValueError):self.apply([self.item(),self.item('missing')])
        self.assertEqual(self.ws.records(),[])

    def test_project_priority_order_and_confirm_reset(self):
        low=self.item(title='low');low.update(priority='P3',priority_confirmed=True,priority_confirmation='用户明确确认原排序')
        high=self.item(title='high');high['priority']='P1'
        self.apply([low,high])
        text=self.ws.path('项目归属索引.md').read_text(encoding='utf-8');self.assertLess(text.index('high'),text.index('low'))
        r=next(r for r in self.ws.records() if r['title']=='low')
        item=dict(id=r['id'],expected_revision=r['revision'],project_id='alpha',title='low',priority='P0',reason='重新评估影响',sections=r['sections'])
        self.apply([item]);self.assertFalse(next(x for x in self.ws.records() if x['id']==r['id'])['priority_confirmed'])

    def test_lock_and_tamper_validation(self):
        with self.ws.lock():
            with self.assertRaises(FileExistsError):
                with self.ws.lock():pass
        self.apply([self.item()]);r=self.ws.records()[0];self.ws.path(r['path']).write_text('tampered',encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()),self.assertRaises(ValueError):m.validate(self.ws)

    def test_weekly_links_resolve(self):
        self.apply([self.item(title='UI 对比 [新增] (a) #50%' ),self.item('beta'),self.item('unassigned')])
        import re
        for p in self.asset.rglob('*.md'):
            for target in re.findall(r'\]\(([^)]+)\)',p.read_text(encoding='utf-8')):
                self.assertTrue((p.parent/target).exists(),str(p)+' '+target)

    def test_progress_change_requires_new_basis(self):
        self.apply([self.item()]);r=self.ws.records()[0]
        item=dict(id=r['id'],expected_revision=1,project_id='alpha',title=r['title'],reason='进度变更',sections=r['sections'],progress='未完成')
        with self.assertRaises(ValueError):self.apply([item])

    def test_v1_migration_never_reads_original_text(self):
        old=self.home/'v1';(old/'records').mkdir(parents=True);(old/'原始输入').mkdir()
        (old/'原始输入'/'raw.md').write_text('SECRET_RAW_SENTINEL',encoding='utf-8')
        item=self.item();record=dict(id='REQ-ALPHA-ABC123',project_id='alpha',title=item['title'],created_at='2026-10-02T10:00:00+08:00',updated_at='2026-10-03T10:00:00+08:00',status='已确认',priority='P2',priority_confirmed=False,revision=3,source_ids=[],sections=item['sections'],related_ids=[])
        (old/'records'/'REQ-ALPHA-ABC123.json').write_text(json.dumps(record,ensure_ascii=False),encoding='utf-8')
        before=self.digest(old)
        original=m.read_json;read_paths=[]
        def spy(p):read_paths.append(str(p));return original(p)
        m.read_json=spy
        try:
            with contextlib.redirect_stdout(io.StringIO()):m.migrate_v1(self.ws,old)
        finally:m.read_json=original
        self.assertFalse(any('原始输入' in p or 'intakes' in p for p in read_paths))
        self.assertEqual(before,self.digest(old));self.assertEqual(self.ws.records()[0]['progress'],'未开始')
        self.assertEqual(self.ws.records()[0]['legacy_revision'],3);self.assertIn('旧进度未知',self.ws.records()[0]['progress_note'])
        self.assertFalse(any(b'SECRET_RAW_SENTINEL' in p.read_bytes() for p in self.asset.rglob('*') if p.is_file()))
        self.validate()

    def test_flat_paths_global_daily_sequence_and_project_correction(self):
        self.clock='2026-09-09T10:00:00+08:00'
        self.apply([self.item('alpha','本地AI需求按工作周管理'),self.item('beta'),self.item('unassigned')])
        records=self.ws.records();self.assertEqual([r['id'] for r in records],['20260909-01','20260909-02','20260909-03'])
        self.assertEqual(records[0]['path'],'09月/09.07-09.11/09.09-01-本地AI需求按工作周管理.md')
        self.assertFalse(self.ws.path('项目').exists());self.assertFalse(self.ws.path('待归属').exists())
        r=records[0];self.clock='2026-09-10T12:00:00+08:00'
        self.apply([dict(id=r['id'],expected_revision=1,project_id='beta',reason='纠正项目归属')])
        revised=self.ws.records()[0];self.assertEqual(revised['path'],r['path']);self.assertEqual(revised['project_id'],'beta')
        self.validate()

    def test_large_requirement_children_and_no_automatic_completion(self):
        item=self.item();item['children']=[dict(title='项目配置',acceptance='可选择项目配置'),dict(title='后处理能力',acceptance='输出后处理结果')]
        self.apply([item]);r=self.ws.records()[0];children=r['children']
        self.assertEqual(children[0]['id'],r['id']+'-S01')
        report=self.ws.path('10月/10.05-10.09/本周需求总汇.md').read_text(encoding='utf-8')
        self.assertIn('| 1 | 2 | 1 | 0 | 0 | 0 | 0 |',report);self.assertIn(children[1]['id'],report)
        with self.assertRaises(ValueError):self.apply([dict(id=r['id'],expected_revision=1,reason='缺少子项确认',status='已解决',progress_source='用户报告',progress_note='父项完成报告',completion_evidence=['父项完成报告'])])
        with self.assertRaises(ValueError):self.apply([dict(id=r['id'],expected_revision=1,reason='尝试删除',children=[])])
        updates=[dict(id=c['id'],status='已解决',progress_source='用户报告',progress_note='用户明确反馈子项完成；未独立验证',completion_evidence=['该子项完成反馈摘要']) for c in children]
        self.clock='2026-10-12T11:00:00+08:00'
        self.apply([dict(id=r['id'],expected_revision=1,reason='登记各子项完成',children=updates)])
        revised=self.ws.records()[0];self.assertEqual(revised['progress'],'未开始')
        self.apply([dict(id=r['id'],expected_revision=2,reason='登记父项完成',status='已解决',progress_source='用户报告',progress_note='用户明确反馈整体完成',completion_evidence=['整体完成反馈摘要'])])
        self.validate();self.assertEqual(len(m.week_state(self.ws,'2026-10-12')['new']),0)
        self.assertEqual(len(m.week_state(self.ws,'2026-10-12')['cross']),2)

    def test_canceled_child_not_effective_and_no_id_reuse(self):
        self.apply([dict(self.item(),children=[dict(title='原子项',acceptance='独立验收')])]);r=self.ws.records()[0]
        self.apply([dict(id=r['id'],expected_revision=1,reason='范围调整',children=[dict(id=r['children'][0]['id'],status='已取消',progress_source='用户报告',progress_note='用户取消该范围'),dict(title='新子项',acceptance='新的验收标准')])])
        r=self.ws.records()[0];self.assertEqual(r['children'][1]['id'],r['id']+'-S02')
        text=self.ws.path('10月/10.05-10.09/本周需求总汇.md').read_text(encoding='utf-8')
        self.assertIn('| 1 | 1 | 1 | 0 | 0 | 0 | 0 |',text)
        self.assertIn('⚫ 已取消',text)

    def test_reopening_resolution_time_and_status_conflict(self):
        self.apply([self.item()]);r=self.ws.records()[0]
        self.clock='2026-10-12T10:00:00+08:00';r=self.update(r,progress='已解决',progress_source='用户报告',progress_note='验收完成报告',completion_evidence=['验收报告摘要'])
        self.clock='2026-10-13T10:00:00+08:00';r=self.update(r,progress='进行中',progress_source='用户报告',progress_note='验收后发现新问题')
        self.assertIsNone(r['resolved_at'])
        self.clock='2026-10-14T10:00:00+08:00';r=self.update(r,progress='已解决',progress_source='用户报告',progress_note='用户再次验收完成',completion_evidence=['第二次验收报告摘要'])
        self.assertEqual(r['resolved_at'],self.clock)
        bad=dict(id=r['id'],expected_revision=r['revision'],reason='矛盾状态',status='已解决',progress='进行中')
        with self.assertRaises(ValueError):self.apply([bad])

    def test_empty_explicit_week_report_survives_reindex(self):
        with self.ws.lock():m.rebuild(self.ws,'2026-09-09')
        with self.ws.lock():m.rebuild(self.ws)
        self.assertTrue(self.ws.path('09月/09.07-09.11/本周需求总汇.md').exists())
        self.assertIn('09.07-09.11',self.ws.path('每周总汇索引.md').read_text(encoding='utf-8'))

    def test_v2_migration_preview_preserves_ids_history_and_old_asset(self):
        old=self.home/'v2';(old/'.reqmgr/records').mkdir(parents=True)
        (old/'.reqmgr/config.json').write_text(json.dumps(dict(version=2)),encoding='utf-8')
        ident='REQ-ALPHA-ABC123';revdir=old/'.reqmgr/revisions'/ident;revdir.mkdir(parents=True)
        item=self.item();record=dict(id=ident,project_id='alpha',title='旧需求',created_at='2026-09-28T21:20:29+08:00',updated_at='2026-09-28T21:20:29+08:00',status='已确认',priority='P2',priority_confirmed=False,revision=1,source_ids=[],sections=item['sections'],related_ids=[],progress='未开始',progress_source='新需求初始化',progress_note='尚未开始',completion_evidence=[],reason='规范化新增原因')
        (revdir/'r0001.json').write_text(json.dumps(record,ensure_ascii=False),encoding='utf-8')
        record.update(revision=2,updated_at='2026-10-08T09:26:34+08:00',progress='已完成',progress_source='用户报告',progress_note='完成报告摘要',completion_evidence=['用户报告完成摘要'],reason='规范化完成原因')
        (revdir/'r0002.json').write_text(json.dumps(record,ensure_ascii=False),encoding='utf-8')
        (old/'.reqmgr/records'/f'{ident}.json').write_text(json.dumps(record,ensure_ascii=False),encoding='utf-8')
        (old/'原始输入').mkdir();(old/'原始输入/raw.md').write_text('DO_NOT_COPY_RAW_SENTINEL',encoding='utf-8')
        before=self.digest(old);target_before=self.digest(self.asset)
        with contextlib.redirect_stdout(io.StringIO()):m.migrate_v2(self.ws,old,True)
        self.assertEqual(target_before,self.digest(self.asset));self.assertEqual(before,self.digest(old))
        with contextlib.redirect_stdout(io.StringIO()):m.migrate_v2(self.ws,old)
        migrated=self.ws.records()[0];self.assertEqual(migrated['id'],ident);self.assertEqual(migrated['revision'],2)
        self.assertEqual(migrated['progress'],'已解决');self.assertEqual(migrated['resolved_at'],'2026-10-08T09:26:34+08:00')
        self.assertEqual(migrated['path'],'09月/09.28-10.02/09.28-01-旧需求.md')
        self.assertEqual(before,self.digest(old));self.assertEqual(len(self.ws.revisions(ident)),2)
        self.assertFalse(any(b'DO_NOT_COPY_RAW_SENTINEL' in p.read_bytes() for p in self.asset.rglob('*') if p.is_file()))
        self.validate()

    def test_migrated_daily_sequence_prevents_filename_reuse(self):
        old=self.home/'v1';(old/'records').mkdir(parents=True)
        record=dict(id='REQ-ALPHA-ABC123',project_id='alpha',title='相同标题',created_at=self.clock,updated_at=self.clock,status='已确认',priority='P2',priority_confirmed=False,revision=1,source_ids=[],sections=self.item()['sections'],related_ids=[])
        (old/'records/REQ-ALPHA-ABC123.json').write_text(json.dumps(record,ensure_ascii=False),encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()):m.migrate_v1(self.ws,old)
        before=self.ws.records()[0];oldpath=self.ws.path(before['path']).read_bytes()
        self.apply([self.item(title='相同标题')]);new=next(r for r in self.ws.records() if r['id'].startswith('20261009'))
        self.assertEqual(new['id'],'20261009-02');self.assertNotEqual(new['path'],before['path'])
        self.assertEqual(oldpath,self.ws.path(before['path']).read_bytes());self.validate()

    def test_child_status_typo_or_conflict_rejected_before_writing(self):
        for child in [dict(title='子项',acceptance='验收',status='已解诀'),dict(title='子项',acceptance='验收',status='已解决',progress='未开始')]:
            with self.assertRaises(ValueError):self.apply([dict(self.item(),children=[child])])
        self.assertEqual(self.ws.records(),[])

    @unittest.skipUnless(shutil.which('git'),'Git required')
    def test_git_snapshot_readonly_and_wrong_project_evidence(self):
        repo=self.src/'alpha'
        def git(*args):return subprocess.run(['git','-C',str(repo),*args],capture_output=True,text=True,check=True).stdout.strip()
        git('init','-b','main');git('add','README.md');git('-c','user.email=fixture@example.invalid','-c','user.name=Fixture','commit','-m','fixture');git('remote','add','origin',str(repo))
        (repo/'README.md').write_text('dirty fixture');before=self.digest(self.src)
        with contextlib.redirect_stdout(io.StringIO()):m.snapshot(self.ws,'alpha',True)
        s=m.read_json(next(self.ws.path('代码依据').glob('*.json')))
        self.assertEqual(s['remote_status'],'已核查');self.assertTrue(s['working_tree']);self.assertEqual(before,self.digest(self.src))
        item=self.item('beta');item['source_ids']=[s['id']]
        with self.assertRaises(ValueError):self.apply([item])

if __name__=='__main__':unittest.main()
