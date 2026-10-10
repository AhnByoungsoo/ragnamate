"""스키마 마이그레이션. 기존 창고/시세 테이블은 건드리지 않고 테이블만 추가한다.

v1: market_price_cache, local_listings, meta (창고 시세 분석기)
v2: Replay 분석 기록 (replay_sessions, session_segments, builds, monster_names)
"""
import logging

log = logging.getLogger(__name__)

MIGRATIONS = {
    2: """
    CREATE TABLE IF NOT EXISTS replay_sessions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        file_hash       TEXT NOT NULL UNIQUE,
        filename        TEXT NOT NULL,
        file_path       TEXT,
        character       TEXT,
        job             TEXT,
        job_id          INTEGER,
        level           INTEGER,
        map             TEXT,
        start_time      TEXT,
        duration_ms     INTEGER,
        window_label    TEXT,
        window_ms       INTEGER,
        damage          INTEGER,
        combat_dps      REAL,
        total_dps       REAL,
        max_hit         INTEGER,
        burst_1s        REAL,
        kills           INTEGER,
        base_exp        INTEGER,
        job_exp         INTEGER,
        base_exp_per_hour REAL,
        zeny_net        INTEGER,
        loot_value      INTEGER,
        consumable_cost INTEGER,
        profit          INTEGER,
        profit_per_hour REAL,
        top_skill       TEXT,
        build_name      TEXT DEFAULT '',
        build_memo      TEXT DEFAULT '',
        report_json     BLOB,             -- zlib 압축 Report (재분석 없이 다시 열기)
        created_at      TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS session_segments (
        session_id      INTEGER NOT NULL REFERENCES replay_sessions(id) ON DELETE CASCADE,
        idx             INTEGER NOT NULL,
        map             TEXT,
        start_ms        INTEGER,
        end_ms          INTEGER,
        is_hunt         INTEGER,
        damage          INTEGER,
        kills           INTEGER,
        base_exp        INTEGER,
        job_exp         INTEGER,
        zeny_net        INTEGER,
        loot_value      INTEGER,
        consumable_cost INTEGER,
        profit          INTEGER,
        PRIMARY KEY (session_id, idx)
    );
    CREATE TABLE IF NOT EXISTS builds (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        name    TEXT NOT NULL UNIQUE,
        memo    TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS monster_names (
        class_id INTEGER PRIMARY KEY,
        name     TEXT NOT NULL
    );
    """,
    # v3: RO Navigator 사용자 상태 (Guide 본문은 data/guides/*.json Guide Pack)
    3: """
    CREATE TABLE IF NOT EXISTS guide_progress (
        character   TEXT NOT NULL DEFAULT '',
        guide_id    TEXT NOT NULL,
        current_step INTEGER NOT NULL DEFAULT 0,
        done_steps  TEXT NOT NULL DEFAULT '[]',
        updated_at  TEXT NOT NULL,
        PRIMARY KEY (character, guide_id)
    );
    CREATE TABLE IF NOT EXISTS quest_states (
        character   TEXT NOT NULL DEFAULT '',
        guide_id    TEXT NOT NULL,
        state       TEXT NOT NULL,            -- COMPLETED / NOT_COMPLETED / UNKNOWN
        source      TEXT NOT NULL DEFAULT 'manual',
        updated_at  TEXT NOT NULL,
        PRIMARY KEY (character, guide_id)
    );
    CREATE TABLE IF NOT EXISTS guide_favorites (
        guide_id    TEXT PRIMARY KEY,
        created_at  TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS guide_history (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        guide_id    TEXT NOT NULL,
        viewed_at   TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS guide_search_misses (
        query       TEXT PRIMARY KEY,
        count       INTEGER NOT NULL DEFAULT 1,
        last_at     TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS route_history (      -- 개인 Replay 이동 기록(공식 Guide Route와 별개)
        file_hash   TEXT NOT NULL,
        t_ms        INTEGER NOT NULL,
        map         TEXT,
        x           INTEGER,
        y           INTEGER
    );
    CREATE INDEX IF NOT EXISTS ix_route ON route_history(file_hash);
    """,
    # v4: Passive Live Mode
    4: """
    CREATE TABLE IF NOT EXISTS live_sessions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        start_time      REAL NOT NULL,
        end_time        REAL,
        data_mode       TEXT NOT NULL,          -- SCREEN / HYBRID / REPLAY
        confidence      TEXT NOT NULL,          -- 화면 기준 / Replay 보강 등
        map             TEXT,
        map_source      TEXT,
        active_sec      REAL,
        elapsed_sec     REAL,
        item_value      INTEGER,
        zeny            INTEGER,
        profit          INTEGER,
        profit_per_hour REAL,
        exp_pct_per_hour REAL,
        kills           INTEGER,
        combat_dps      REAL,
        detail_json     TEXT
    );
    CREATE TABLE IF NOT EXISTS game_events (
        session_id  INTEGER,
        ts          REAL NOT NULL,
        event_type  TEXT NOT NULL,
        source      TEXT NOT NULL,
        confidence  TEXT NOT NULL,
        item_id     INTEGER,
        item_name   TEXT,
        quantity    INTEGER,
        value       REAL,
        text        TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_game_events ON game_events(session_id);
    CREATE TABLE IF NOT EXISTS screen_profiles (
        name        TEXT PRIMARY KEY,
        data        TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS ocr_diagnostics (
        ts          REAL NOT NULL,
        region      TEXT,
        text        TEXT,
        confidence  REAL,
        note        TEXT
    );
    """,
}
LATEST = max(MIGRATIONS)


def migrate(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)')
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    ver = int(row[0]) if row else 1
    for v in sorted(k for k in MIGRATIONS if k > ver):
        log.info('DB 마이그레이션 v%d → v%d', ver, v)
        conn.executescript(MIGRATIONS[v])
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (str(v),))
        conn.commit()
        ver = v
    return ver
