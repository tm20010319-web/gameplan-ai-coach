"""Download official hero icons for local BP matching. No game frames are sent."""
import concurrent.futures
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LIST_URL = 'https://pvp.qq.com/web201605/js/herolist.json'


def main():
    dest = ROOT / 'data' / 'hero-portraits'
    dest.mkdir(parents=True, exist_ok=True)
    heroes = json.loads(urllib.request.urlopen(LIST_URL, timeout=25).read().decode('utf-8-sig'))
    def download(hero):
        ident = int(hero['ename'])
        url = f'https://game.gtimg.cn/images/yxzj/img201606/heroimg/{ident}/{ident}.jpg'
        raw = urllib.request.urlopen(url, timeout=25).read(1000000)
        from PIL import Image
        import io
        Image.open(io.BytesIO(raw)).verify()
        (dest / f'{ident}.jpg').write_bytes(raw)
        return {'hero':hero['cname'],'id':ident,'file':f'{ident}.jpg','source':url}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        entries = list(pool.map(download, heroes))
    (dest / 'manifest.json').write_text(json.dumps(entries,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Downloaded {len(entries)} official hero portraits; restart server to reload them.')


if __name__ == '__main__':
    main()
