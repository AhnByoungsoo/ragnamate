import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import ROOT, PLAYER, ev
from analyzer.events import ESTIMATE
from analyzer.gamedata import _NameFile, parse_efst_enum
from core.display import CONFIRMED, DERIVED, UNKNOWN, DisplayNames
from core.items import ItemDB
from live.learn import BuffOriginLearner


class MatchingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def names(self, label='스킬', fallback=None):
        return _NameFile(str(self.root / 'names.json'), label, fallback)

    def write(self, name, data):
        path = self.root / name
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        return str(path)

    def test_unknown_item_is_not_confirmed_and_string_ids_match(self):
        items = ItemDB('', str(self.root / 'items.json'))
        items.items[501] = dict(name='빨간포션', slot=0, description='')
        d = DisplayNames(None, items)
        self.assertEqual(d.item(99999).confidence, UNKNOWN)
        self.assertEqual(d.item('501').text, '빨간포션')
        self.assertEqual(d.item(True).confidence, UNKNOWN)
        self.assertEqual(d.item(501.5).confidence, UNKNOWN)

    def test_yaml_import_does_not_become_user_confirmed_on_reload(self):
        path = self.root / 'skill.yml'
        path.write_text('  - Id: 28\n    Description: Heal\n', encoding='utf-8')
        names = self.names()
        self.assertEqual(names.import_file(str(path)), 1)
        self.assertEqual(names.origin(28), 'rathena')
        self.assertEqual(self.names().origin('28'), 'rathena')
        self.assertEqual(names.import_file(str(path)), 0)

    def test_skill_yaml_cannot_be_loaded_as_efst_ids(self):
        with self.assertRaises(ValueError):
            self.names('버프').import_rathena_yml(str(self.root / 'skill.yml'))

    def test_structured_import_preserves_name_not_dict_repr(self):
        path = self.write('input.json', {'28': {'name':'힐', 'src':'user'}, 'bad': 'bad', '29': None})
        names = self.names()
        self.assertEqual(names.import_file(path), 1)
        self.assertEqual(names.name(28), '힐')
        self.assertEqual(names.origin(28), 'import')

    def test_bad_record_does_not_hide_later_good_record(self):
        self.write('names.json', {'bad':'x', '28':{'name':None}, '29':'민첩성 증가'})
        self.assertEqual(self.names().name(29), '민첩성 증가')

    def test_official_name_preferred_to_old_external_import(self):
        fallback = self.write('official.json', {'28':dict(name='힐', src='official_kro')})
        self.write('names.json', {'28':dict(name='Heal', src='rathena')})
        self.assertEqual(self.names(fallback=fallback).name(28), '힐')

    def test_official_fallback_user_override_and_clear(self):
        path = self.write('official.json', {'28':dict(name='힐',src='official_kro',url='https://example.com',checked_at='2026-09-25')})
        names = self.names(fallback=path)
        self.assertEqual(names.origin(28), 'official_kro')
        names.set(28, '사용자 힐')
        self.assertEqual(self.names(fallback=path).name(28), '사용자 힐')
        names.set(28, '')
        again = self.names(fallback=path)
        self.assertEqual(again.name(28), '힐')
        self.assertEqual(again.metadata(28)['checked_at'], '2026-09-25')

    def test_enum_comments_aliases_and_unsupported_expression(self):
        enum = 'enum efst_type { EFST_A=10, /* multi\nline */ EFST_B, EFST_C=EFST_A+4, EFST_D,\n};'
        self.assertEqual(parse_efst_enum(enum)[15], 'EFST_D')
        with self.assertRaises(ValueError):
            parse_efst_enum('enum efst_type { EFST_A=10, EFST_B=MISSING, EFST_C,\n};')

    def test_same_mtime_modified_client_invalidates_item_cache(self):
        system = self.root / 'System'
        system.mkdir()
        lub = system / 'itemInfo_true.lub'
        lub.write_bytes(b'first')
        cache = str(self.root / 'item.json')
        item = lambda name: {501:dict(name=name,slot=0,description='')}
        with patch('core.items.iteminfo.load_items', return_value=item('첫 이름')) as load:
            ItemDB(str(self.root), cache).load()
            self.assertEqual(load.call_count, 1)
        stamp = lub.stat()
        lub.write_bytes(b'other')
        os.utime(lub, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        with patch('core.items.iteminfo.load_items', return_value=item('바뀐 이름')) as load:
            self.assertEqual(ItemDB(str(self.root), cache).load().name(501), '바뀐 이름')
            self.assertEqual(load.call_count, 1)
        with patch('core.items.iteminfo.load_items') as load:
            self.assertEqual(ItemDB(str(self.root), cache).load().name(501), '바뀐 이름')
            load.assert_not_called()

    def learner(self):
        return BuffOriginLearner(str(self.root / 'origins.json'))

    def test_duplicate_analysis_append_restart_and_server_scope(self):
        events = [ev(1000,'SKILL_USE',PLAYER,skill=28),ev(1050,'BUFF_START',PLAYER,efst=10)]
        m = self.learner()
        self.assertEqual(m.observe_events(events, PLAYER, recording_id='one', context='A'), 1)
        m = self.learner()
        self.assertEqual(m.observe_events(events, PLAYER, recording_id='one', context='A'), 0)
        events += [ev(2000,'SKILL_USE',PLAYER,skill=28),ev(2050,'BUFF_START',PLAYER,efst=10)]
        self.assertEqual(m.observe_events(events, PLAYER, recording_id='one', context='A'), 1)
        self.assertEqual(m.top(10, context='A')[1], 2)
        self.assertIsNone(m.top(10, context='B'))
        self.assertEqual(m.observe_events(events[:2], PLAYER, recording_id='two', context='A'), 1)

    def test_cast_only_postdelay_snapshot_and_foreign_target_not_learned(self):
        cases = [
            [ev(0,'SKILL_CAST_START',PLAYER,skill=28),ev(50,'BUFF_START',PLAYER,efst=10)],
            [ev(0,'SKILL_USE',PLAYER,skill=28),ev(50,'BUFF_START',PLAYER,efst=46)],
            [ev(0,'SKILL_USE',PLAYER,skill=28),ev(50,'BUFF_START',PLAYER,efst=10,in_view=True)],
            [ev(0,'SKILL_USE',PLAYER,target=999,skill=28),ev(50,'BUFF_START',PLAYER,efst=10)],
            [ev(0,'SKILL_USE',PLAYER,skill=28),ev(30,'SKILL_USE',999,target=PLAYER,skill=29),ev(50,'BUFF_START',PLAYER,efst=10)],
            [ev(0,'SKILL_USE',PLAYER,map_name='a',skill=28),ev(50,'BUFF_START',PLAYER,map_name='b',efst=10)],
        ]
        for events in cases:
            with self.subTest(events=events):
                self.assertEqual(self.learner().observe_events(events, PLAYER), 0)

    def test_experimental_event_cannot_raise_candidate_count(self):
        skill = ev(0,'SKILL_USE',PLAYER,skill=28)
        skill.confidence = ESTIMATE
        self.assertEqual(self.learner().observe_events([skill,ev(50,'BUFF_START',PLAYER,efst=10)], PLAYER), 0)

    def test_legacy_counts_retained_but_not_ranked(self):
        old = {'10':{'28':{'count':9000, 'last_gap_ms':0}}}
        self.write('origins.json', {'origins':old})
        m = self.learner()
        self.assertIsNone(m.top(10))
        m.save()
        self.assertEqual(self.learner().legacy, old)

    def test_multiple_status_packets_for_one_use_count_once(self):
        events = [ev(0,'SKILL_USE',PLAYER,skill=28),ev(10,'BUFF_START',PLAYER,efst=10),
                  ev(20,'BUFF_START',PLAYER,efst=10,remain_ms=5000)]
        m = self.learner()
        self.assertEqual(m.observe_events(events, PLAYER), 1)
        self.assertEqual(m.top(10)[1], 1)

    def test_official_catalog_uses_skill_ids_and_valid_korean(self):
        path = os.path.join(ROOT, 'data', 'skill_names_official.json')
        names = self.names(fallback=path)
        self.assertEqual(names.name(34), '블레싱')
        self.assertEqual(names.name('28'), '힐')
        self.assertTrue(all('\ufffd' not in name for name in names.names.values()))
        self.assertGreater(len(names.names), 1000)

    def test_reference_connection_is_not_confirmed_or_same_numeric_namespace(self):
        buffs = self.names('버프')
        buffs.names[10], buffs.srcs[10] = 'BLESSING', 'rathena'
        game = type('G', (), dict(buffs=buffs, items=None, buff_links={'10':dict(symbol='BLESSING',name='블레싱 효과')}))()
        display = DisplayNames(game)
        self.assertEqual(display.status(10).text, '블레싱 효과')
        self.assertEqual(display.status(10).confidence, DERIVED)
        buffs.set(10, '내 이름')
        self.assertEqual(display.status(10).confidence, CONFIRMED)
        self.assertEqual(display.status(10).text, '내 이름')
