"""Catalog precedence, separate ID namespaces, and offline icon safety."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from helpers import ROOT
from analyzer.gamedata import _NameFile, GameDataProvider
from core.display import DisplayNames, DERIVED
from core.icon_catalog import bundled_icon, canonical_key, _manifest


class SkillCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p = Path(self.tmp.name)

    def write(self, name, obj):
        path = self.p / name
        path.write_text(json.dumps(obj, ensure_ascii=False), encoding='utf-8')
        return str(path)

    def test_old_import_cannot_replace_client_title_but_user_can(self):
        catalog = self.write('catalog.json', {'10': dict(name='블레싱', src='client_kro', symbol='EFST_BLESSING')})
        path = self.write('names.json', {'10': dict(name='BLESSING', src='rathena')})
        names = _NameFile(path, '버프', catalog)
        self.assertEqual(names.name(10), '블레싱')
        self.assertEqual(names.import_efst('enum efst_type { EFST_BLESSING=10,\n};'), 0)
        names.set(10, '내 블레싱')
        self.assertEqual(_NameFile(path, '버프', catalog).name(10), '내 블레싱')
        names.set(10, '')
        self.assertEqual(_NameFile(path, '버프', catalog).metadata(10)['symbol'], 'EFST_BLESSING')

    def test_translated_title_keeps_provenance_and_internal_flag(self):
        catalog = self.write('catalog.json', {'46':dict(name='스킬 대기',src='client_kro',symbol='EFST_POSTDELAY',url='https://example.test/source')})
        names = _NameFile(str(self.p/'user.json'), '버프', catalog)
        result = DisplayNames(SimpleNamespace(buffs=names)).status(46)
        self.assertEqual(result.text, '스킬 대기')
        self.assertTrue(result.internal)
        self.assertEqual(result.confidence, DERIVED)
        self.assertIn('https://example.test/source',result.tooltip())

    def test_different_numeric_namespaces_never_share_by_number(self):
        (self.p/'skill.png').write_bytes(b'test')
        self.write('icon_catalog.json', {'skill_10': dict(path='skill.png')})
        self.assertIsNotNone(bundled_icon('skill_10',str(self.p)))
        self.assertIsNone(bundled_icon('efst_10',str(self.p)))
        self.assertEqual(canonical_key('replay_10'), 'efst_10')
        self.assertIsNone(canonical_key('manual_10'))

    def test_manifest_cannot_escape_catalog_root(self):
        self.write('icon_catalog.json', {'skill_1':dict(path='../outside.png'), 'efst_1':dict(path=__file__)})
        self.assertIsNone(bundled_icon('skill_1',str(self.p)))
        self.assertIsNone(bundled_icon('efst_1',str(self.p)))
        self.assertIsNone(bundled_icon('../skill_1',str(self.p)))

    def test_missing_manifest_is_safe_offline(self):
        self.assertIsNone(bundled_icon('skill_99999',str(self.p)))

    def test_shipped_catalog_fixes_6513_and_multiple_jobs(self):
        data = Path(ROOT)/'data'
        names = _NameFile(str(self.p/'skill.json'),'스킬',str(data/'skill_catalog.json'))
        self.assertEqual(names.name(6513),'체이싱 브레이크')
        for ident in [28, 2327, 5245, 5217, 5401, 5507]:
            self.assertFalse(names.name(ident).startswith('스킬 #'),ident)
        self.assertEqual(names.metadata(6513)['symbol'],'ABC_CHASING_BREAK')
        buffs = _NameFile(str(self.p/'buff.json'),'버프',str(data/'buff_catalog.json'))
        self.assertIn('블레싱', buffs.name(10))
        self.assertEqual(buffs.metadata(10)['symbol'],'EFST_BLESSING')
        for ident,name in buffs.names.items():
            self.assertNotIn('%s',name,ident)


if __name__ == '__main__':
    unittest.main()
