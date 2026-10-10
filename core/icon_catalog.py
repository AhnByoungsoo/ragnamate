"""Namespaced, local-only game icon lookup. No network calls from the UI."""
import json
import os
import re
from functools import lru_cache

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')

def canonical_key(key):
    match = re.fullmatch(r'(skill|efst|replay)_(\d+)', str(key))
    if not match:
        return None
    kind, ident = match.groups()
    return '%s_%d' % ('efst' if kind == 'replay' else kind, int(ident))

@lru_cache(maxsize=8)
def _manifest(data_dir):
    try:
        with open(os.path.join(data_dir, 'icon_catalog.json'), encoding='utf-8') as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}

def icon_metadata(key, data_dir=DATA_DIR):
    value = _manifest(os.path.abspath(data_dir)).get(canonical_key(key), {})
    return dict(value) if isinstance(value, dict) else {}

def bundled_icon(key, data_dir=DATA_DIR):
    rel = icon_metadata(key, data_dir).get('path')
    if not isinstance(rel, str) or os.path.isabs(rel):
        return None
    root = os.path.realpath(data_dir)
    path = os.path.realpath(os.path.join(root, rel))
    try:
        if os.path.commonpath([root, path]) != root:
            return None
    except ValueError:
        return None
    return path if os.path.isfile(path) else None
