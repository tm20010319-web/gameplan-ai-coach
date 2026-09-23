"""Archive user-supplied Markdown as pending evidence, never as model instructions."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

ROOT = Path(__file__).resolve().parents[1]


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
    with sqlite3.connect(root / 'data' / 'hero_knowledge.sqlite3') as db:
        db.execute('''CREATE TABLE IF NOT EXISTS source_documents (
            id TEXT PRIMARY KEY, hero TEXT NOT NULL, lane TEXT, filename TEXT,
            archive_path TEXT, imported_at TEXT, status TEXT NOT NULL,
            source_urls TEXT, author TEXT, game_version TEXT)''')
        db.execute('''CREATE TABLE IF NOT EXISTS source_sections (
            document_id TEXT NOT NULL, ordinal INTEGER NOT NULL, heading TEXT,
            content TEXT NOT NULL, status TEXT NOT NULL,
            PRIMARY KEY(document_id, ordinal))''')
        urls = re.findall(r'https?://[^\s<>\)]+', content)
        db.execute('INSERT OR IGNORE INTO source_documents VALUES (?,?,?,?,?,?,?,?,?,?)',
                   (digest, hero, lane, Path(path).name, str(archive.relative_to(root)),
                    datetime.now(timezone.utc).isoformat(), 'pending_review', json.dumps(urls), None, None))
        for index, (title, text) in enumerate(sections):
            db.execute('INSERT OR IGNORE INTO source_sections VALUES (?,?,?,?,?)',
                       (digest, index, title, text, 'pending_review'))
    return {'document_id': digest, 'sections': len(sections), 'status': 'pending_review',
            'archive_path': str(archive / 'source.md')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path')
    parser.add_argument('--hero', required=True)
    parser.add_argument('--lane', default='unknown')
    args = parser.parse_args()
    print(json.dumps(import_document(args.path, args.hero, args.lane), ensure_ascii=True))
