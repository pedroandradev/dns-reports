"""SQLite schema and backward-compatible migrations."""
import sqlite3
from .config import BASE, config
def db_connect():
    c = sqlite3.connect(str(BASE / 'data' / 'queries.sqlite3'), timeout=30)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA busy_timeout=30000')
    c.execute('PRAGMA journal_mode=WAL')
    return c


def create_schema(c):
    c.executescript('''
      CREATE TABLE IF NOT EXISTS queries (
        uid TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        ip TEXT NOT NULL,
        hostname TEXT NOT NULL,
        domain TEXT NOT NULL,
        service TEXT NOT NULL,
        category TEXT NOT NULL,
        qtype TEXT NOT NULL,
        status TEXT NOT NULL,
        blocked INTEGER NOT NULL,
        reason TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_queries_ts ON queries(ts);
      CREATE INDEX IF NOT EXISTS idx_queries_ip_ts ON queries(ip,ts);
      CREATE INDEX IF NOT EXISTS idx_queries_domain_ts ON queries(domain,ts);
      CREATE INDEX IF NOT EXISTS idx_queries_service_ts ON queries(service,ts);
      CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash TEXT NOT NULL,
        session_version INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        is_admin INTEGER NOT NULL DEFAULT 0
      );
      CREATE TABLE IF NOT EXISTS client_names (
        ip TEXT PRIMARY KEY,
        hostname TEXT NOT NULL,
        last_checked INTEGER NOT NULL
      );
    ''')
    from .traffic.storage import create_tables
    create_tables(c)
    from .dns_evidence import create_table
    create_table(c)
    # Safe, additive SQLite migration for existing multi-user installations.
    columns = {r['name'] for r in c.execute('PRAGMA table_info(users)').fetchall()}
    if 'is_admin' not in columns:
        c.execute('ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0')
    # Existing installations have a single administrator in config.json.
    # Import it exactly once without changing the credentials or existing data.
    legacy = config()
    if not c.execute("SELECT 1 FROM metadata WHERE key='users_imported'").fetchone():
        if legacy.get('panel_username') and legacy.get('panel_password_hash'):
            c.execute('INSERT OR IGNORE INTO users(username,password_hash) VALUES(?,?)',
                      (legacy['panel_username'],legacy['panel_password_hash']))
        c.execute("INSERT OR IGNORE INTO metadata(key,value) VALUES('users_imported','true')")
    # Assign administration only ONCE to the account originally set up by the installer.
    # Other existing users remain read-only for user management. Re-running the
    # migration cannot promote a later account with the same username.
    if not c.execute("SELECT 1 FROM metadata WHERE key='admin_role_migrated_v1'").fetchone():
        admin_username = legacy.get('panel_username') or 'admin'
        c.execute('UPDATE users SET is_admin=1 WHERE username=? COLLATE NOCASE',
                  (admin_username,))
        c.execute("INSERT INTO metadata(key,value) VALUES('admin_role_migrated_v1','true')")
    c.commit()
