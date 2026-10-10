"""로컬 Guide Pack(JSON) Provider. GuideRepository가 data/guides 아래 파일을 직접 읽으므로
여기서는 Pack 목록/메타 정보만 제공한다."""
import glob
import json
import os


def list_packs(guide_dir):
    out = []
    for f in sorted(glob.glob(os.path.join(guide_dir, '*.json')) + glob.glob(os.path.join(guide_dir, 'packs', '*.json')) +
                    glob.glob(os.path.join(guide_dir, 'user', '*.json'))):
        if os.path.basename(f) in ('job_tree.json', 'aliases.json'):
            continue
        try:
            with open(f, encoding='utf-8') as fh:
                d = json.load(fh)
            out.append(dict(file=f, pack=d.get('pack'), guides=len(d.get('guides', [])),
                            generated_at=d.get('generated_at') or d.get('exported_at')))
        except Exception as e:
            out.append(dict(file=f, pack='(읽기 실패)', guides=0, generated_at=str(e)))
    return out
