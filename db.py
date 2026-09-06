# db.py
import sqlite3
import json
import logging
from datetime import datetime

DB_PATH = 'experiments.db'
logger = logging.getLogger(__name__)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS experiments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            config_name TEXT,
            params TEXT,
            human INTEGER,
            likely_human INTEGER,
            likely_ai INTEGER,
            ai INTEGER,
            timestamp TEXT,
            status TEXT,
            revised_text TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS experiment_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    conn.commit()
    conn.close()
    logger.info("Database initialized")

def save_experiment(config_name, params, results, status='done', revised_text=''):
    logger.info(f"SAVE_EXPERIMENT called: {config_name}")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute('''
            INSERT INTO experiments (config_name, params, human, likely_human, likely_ai, ai, timestamp, status, revised_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            config_name,
            json.dumps(params),
            results.get('human', 0),
            results.get('likely_human', 0),
            results.get('likely_ai', 0),
            results.get('ai', 0),
            datetime.now().isoformat(),
            status,
            revised_text
        ))
        conn.commit()
        last_id = c.lastrowid
        logger.info(f"SAVE_EXPERIMENT success, lastrowid: {last_id}")
        return last_id
    except Exception as e:
        logger.error(f"SAVE_EXPERIMENT failed: {e}")
        conn.rollback()
        return None
    finally:
        conn.close()

def get_all_experiments():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT * FROM experiments ORDER BY id')
    rows = c.fetchall()
    conn.close()
    return rows

def get_best_experiment():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT *, (human + likely_human) as total 
        FROM experiments 
        ORDER BY total DESC 
        LIMIT 1
    ''')
    row = c.fetchone()
    conn.close()
    return row

def set_state(key, value):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('REPLACE INTO experiment_state (key, value) VALUES (?, ?)', (key, value))
    conn.commit()
    conn.close()
    logger.debug(f"State set: {key}={value}")

def get_state(key):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT value FROM experiment_state WHERE key = ?', (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def seed_experiments():
    # ... (без изменений, как было ранее)
    pass
