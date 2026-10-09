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

class V2Tests(unittest.TestCase):
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
        self.assertTrue(str(m.bucket('2026-11-01T09:00:00+08:00')).startswith('2026-10'))
        self.assertTrue(str(m.bucket('2027-01-01T09:00:00+08:00')).startswith('2026-12'))
        self.assertTrue(str(m.bucket('2026-10-09T18:00:00+00:00')).endswith('2026-10-10'))
        with self.assertRaises(ValueError):m.timestamp('2026-10-09T23:00:00')

    def test_requirement_confirmation_is_not_completion(self):
        item=self.item();item['status']='已确认';self.apply([item])
        self.assertEqual(self.ws.records()[0]['progress'],'未开始')
        bad=self.item();bad['progress']='已完成'
        with self.assertRaises(ValueError):self.apply([bad])

    def test_three_progress_colors_and_completion_basis(self):
        incomplete=self.item('beta');incomplete.update(progress='未完成',progress_source='用户报告',progress_note='用户报告进行中，未独立验证')
        complete=self.item('alpha','已完成演示');complete.update(progress='已完成',progress_source='用户报告',progress_note='用户报告已完成，未独立验证',completion_evidence=['用户报告已核对目标行为；记录为报告摘要'])
        self.apply([self.item(),incomplete,complete]);self.validate()
        text=self.ws.path('需求总览.md').read_text(encoding='utf-8')
        for icon in ('🔵 未开始','🟠 未完成','🟢 已完成'):self.assertIn(icon,text)
        self.assertIn('color:#2563eb',text);self.assertIn('color:#d97706',text);self.assertIn('color:#15803d',text)
        state=m.week_state(self.ws,self.clock);self.assertEqual(len(state['completed']),1)

    def test_backlog_survives_empty_new_week(self):
        self.clock='2026-10-02T10:00:00+08:00';self.apply([self.item()])
        self.clock='2026-10-09T23:32:14+08:00'
        with contextlib.redirect_stdout(io.StringIO()):
            with self.ws.lock():m.rebuild(self.ws)
        state=m.week_state(self.ws,self.clock)
        self.assertEqual(len(state['new']),0);self.assertEqual(len(state['carry']),1)
        p=self.ws.path(Path('周总汇')/m.week_folder(self.clock)/'每周需求总汇.md')
        self.assertIn('历史遗留',p.read_text(encoding='utf-8'))
        self.assertIn(self.ws.records()[0]['id'],p.read_text(encoding='utf-8'))

    def test_history_cutoff_and_completion_not_double_counted(self):
        self.clock='2026-10-02T10:00:00+08:00';self.apply([self.item()]);r=self.ws.records()[0]
        self.clock='2026-10-09T10:00:00+08:00'
        r=self.update(r,progress='已完成',progress_source='用户报告',progress_note='用户报告已完成',completion_evidence=['用户报告验收结果摘要'])
        self.assertEqual(m.week_state(self.ws,'2026-10-02')['all'][0]['progress'],'未开始')
        self.assertEqual(len(m.week_state(self.ws,'2026-10-09')['completed']),1)
        self.clock='2026-10-16T10:00:00+08:00';self.update(r,title='仅更改标题')
        self.assertEqual(len(m.week_state(self.ws,'2026-10-16')['completed']),0)
        self.assertEqual(m.week_state(self.ws,'2026-10-09')['all'][0]['progress'],'已完成')

    def test_late_historical_entry_is_unknown(self):
        self.apply([self.item()],received='2026-10-02T10:00:00+08:00')
        state=m.week_state(self.ws,'2026-10-02')
        self.assertEqual(state['all'][0]['progress'],'待核实')

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
        text=self.ws.path('项目/alpha/需求索引.md').read_text(encoding='utf-8');self.assertLess(text.index('high'),text.index('low'))
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
        self.apply([self.item(title='UI 对比 [新增]'),self.item('beta'),self.item('unassigned')])
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
        self.assertEqual(before,self.digest(old));self.assertEqual(self.ws.records()[0]['progress'],'待核实')
        self.assertEqual(self.ws.records()[0]['legacy_revision'],3)
        self.assertFalse(any(b'SECRET_RAW_SENTINEL' in p.read_bytes() for p in self.asset.rglob('*') if p.is_file()))
        self.validate()

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
