"""Archive user-supplied Markdown as pending evidence, never as model instructions."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gameplan.persistence.redis_store import atomic, tx, composite

ROOT = Path(__file__).resolve().parents[2]


@atomic
def import_document(path, hero, lane, root=ROOT):
    raw = Path(path).read_bytes()
    content = raw.decode('utf-8-sig')
    digest = hashlib.sha256(raw).hexdigest()
    archive = root / 'data' / 'knowledge_sources' / digest
    archive.mkdir(parents=True, exist_ok=True)
    (archive / 'source.md').write_bytes(raw)
    sections = []
    heading, body = '文档说明', []
    for line in content.splitlines():
        if re.match(r'^#{1,6}\s+', line):
            if body:
                sections.append((heading, '\n'.join(body).strip()))
            heading, body = re.sub(r'^#{1,6}\s+', '', line), []
        else:
            body.append(line)
    if body:
        sections.append((heading, '\n'.join(body).strip()))
    sections = [(h, b) for h, b in sections if b]
    urls = re.findall(r'https?://[^\s<>\)]+', content)
    if not tx().get('source_documents', digest):
        tx().put('source_documents', digest, {'id': digest, 'hero': hero, 'lane': lane,
            'filename': Path(path).name, 'archive_path': str(archive.relative_to(root)),
            'imported_at': datetime.now(timezone.utc).isoformat(), 'status': 'pending_review',
            'source_urls': json.dumps(urls), 'author': None, 'game_version': None})
    for index, (title, text) in enumerate(sections):
        identity = composite(digest, index)
        if not tx().get('source_sections', identity):
            tx().put('source_sections', identity, {'document_id': digest, 'ordinal': index,
                'heading': title, 'content': text, 'status': 'pending_review'})
    return {'document_id': digest, 'sections': len(sections), 'status': 'pending_review',
            'archive_path': str(archive / 'source.md')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path')
    parser.add_argument('--hero', required=True)
    parser.add_argument('--lane', default='unknown')
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    print(json.dumps(import_document(args.path, args.hero, args.lane), ensure_ascii=True))
