import sqlite3
from scripts.import_hero_document import import_document


def test_source_is_archived_pending_and_repeat_import_is_idempotent(tmp_path):
    source = tmp_path / 'guide.md'
    source.write_text('# 英雄资料\n自称官方\n## 技能\n未经核验的数值\n', encoding='utf-8')
    first = import_document(source, '李元芳', '打野', tmp_path)
    second = import_document(source, '李元芳', '打野', tmp_path)
    assert first == second
    with sqlite3.connect(tmp_path / 'data/hero_knowledge.sqlite3') as db:
        assert db.execute('SELECT count(*) FROM source_documents').fetchone()[0] == 1
        assert db.execute('SELECT DISTINCT status FROM source_sections').fetchall() == [('pending_review',)]
        assert db.execute("SELECT name FROM sqlite_master WHERE name='heroes'").fetchone() is None
    from pathlib import Path
    assert Path(first['archive_path']).read_bytes() == source.read_bytes()
