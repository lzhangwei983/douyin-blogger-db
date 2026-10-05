import sqlite3
from pathlib import Path

import app
import storage


def create_v1_0_6_schema(path):
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE bloggers (
      id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE NOT NULL,
      name TEXT NOT NULL, platform TEXT DEFAULT '抖音', douyin_id TEXT,
      homepage_url TEXT, bio TEXT, notes TEXT, tags TEXT DEFAULT '',
      created_at TEXT, updated_at TEXT
    );
    CREATE TABLE videos (
      id INTEGER PRIMARY KEY AUTOINCREMENT, blogger_id INTEGER NOT NULL REFERENCES bloggers(id),
      seq INTEGER, video_id TEXT UNIQUE, url TEXT NOT NULL, kind TEXT DEFAULT '视频',
      title TEXT, upload_date TEXT, duration INTEGER, like_count INTEGER DEFAULT 0,
      comment_count INTEGER DEFAULT 0, repost_count INTEGER DEFAULT 0,
      save_count INTEGER DEFAULT 0, status TEXT DEFAULT 'ok', subtitle TEXT,
      subtitle_chars INTEGER DEFAULT 0, has_analysis INTEGER DEFAULT 0, images TEXT,
      notes TEXT, tags TEXT DEFAULT '', created_at TEXT, updated_at TEXT
    );
    CREATE TABLE analyses (
      id INTEGER PRIMARY KEY AUTOINCREMENT, video_id INTEGER NOT NULL UNIQUE REFERENCES videos(id),
      full_md TEXT, summary TEXT, key_points TEXT, advice TEXT, industries TEXT,
      risks TEXT, credibility TEXT, actionable TEXT, parsed_at TEXT
    );
    INSERT INTO bloggers(slug,name,created_at) VALUES('legacy','旧库博主','2026-10-01');
    INSERT INTO videos(blogger_id,seq,video_id,url,title,notes,subtitle)
      VALUES(1,1,'123456','https://www.douyin.com/video/123456','人工标题','保留笔记','校对字幕');
    INSERT INTO analyses(video_id,full_md,summary) VALUES(1,'## 内容摘要\n旧分析','旧摘要');
    PRAGMA user_version=0;
    """)
    con.commit()
    con.close()


def test_schema_migration_backs_up_old_database_before_alter_and_preserves_data(tmp_path, monkeypatch):
    database = tmp_path / 'legacy.db'
    create_v1_0_6_schema(database)
    monkeypatch.setattr(app, 'DB_PATH', database)
    monkeypatch.setattr(storage, 'BASE_DIR', tmp_path)

    app.init_db()

    backups = list((tmp_path / 'backups').glob('*/before-schema-migration/legacy.db'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as old:
        assert old.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert old.execute('SELECT notes,subtitle FROM videos').fetchone() == ('保留笔记', '校对字幕')
        assert 'manual_fields' not in {row[1] for row in old.execute('PRAGMA table_info(videos)')}
    with sqlite3.connect(database) as current:
        assert current.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert current.execute('PRAGMA user_version').fetchone()[0] == app.SCHEMA_VERSION
        assert current.execute('SELECT notes,subtitle FROM videos').fetchone() == ('保留笔记', '校对字幕')
        assert current.execute('SELECT full_md FROM analyses').fetchone()[0] == '## 内容摘要\n旧分析'
        assert 'manual_fields' in {row[1] for row in current.execute('PRAGMA table_info(videos)')}

    app.init_db()
    assert len(list((tmp_path / 'backups').glob('*/before-schema-migration/legacy.db'))) == 1


def test_first_run_does_not_create_a_database_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'DB_PATH', tmp_path / 'new.db')
    monkeypatch.setattr(storage, 'BASE_DIR', tmp_path)

    app.init_db()

    assert not (tmp_path / 'backups').exists()
