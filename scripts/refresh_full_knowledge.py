"""Fetch a reproducible official snapshot for the currently supported roster."""
import concurrent.futures
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.update_skill_catalog import fetch, LIST_URL, DETAIL_URL


def main():
    catalog = json.loads((ROOT / 'resources/knowledge/skill_catalog.json').read_text(encoding='utf-8'))
    live = json.loads(fetch(LIST_URL))
    supported = {h['hero'] for h in catalog['heroes']}
    selected = [h for h in live if h['cname'] in supported]
    if {h['cname'] for h in selected} != supported:
        raise ValueError('Official roster no longer contains all supported heroes')
    directory = ROOT / 'work' / ('knowledge-snapshot-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S'))
    directory.mkdir(parents=True)
    (directory / 'herolist.json').write_text(json.dumps(selected, ensure_ascii=False), encoding='utf-8')
    (directory / 'official-herolist.json').write_text(json.dumps(live, ensure_ascii=False), encoding='utf-8')
    def download(hero):
        data = json.loads(fetch(DETAIL_URL.format(hero_id=hero['ename'])))
        if str(data.get('ename')) != str(hero['ename']) or data.get('cname') != hero['cname']:
            raise ValueError('Hero identity mismatch')
        (directory / f"{hero['ename']}.json").write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(download, selected))
    items = json.loads(fetch('https://pvp.qq.com/web201605/js/item.json'))
    payload = {'fetched_at': datetime.now(timezone.utc).isoformat(),
               'source_url': 'https://pvp.qq.com/web201605/js/item.json',
               'current_patch_verified': False, 'items': items}
    (directory / 'items.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'snapshot': str(directory), 'heroes': len(selected),
                      'additional_official_entries': [h['cname'] for h in live if h['cname'] not in supported]}, ensure_ascii=False))

if __name__ == '__main__':
    main()
