# db.py
import sqlite3
import json
from datetime import datetime

DB_PATH = 'experiments.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS experiments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            config_name TEXT,
            params TEXT,                -- JSON с параметрами
            human INTEGER,
            likely_human INTEGER,
            likely_ai INTEGER,
            ai INTEGER,
            timestamp TEXT,
            status TEXT                 -- 'running', 'done', 'failed'
        )
    ''')
    # Таблица для хранения текущего состояния цикла
    c.execute('''
        CREATE TABLE IF NOT EXISTS experiment_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    conn.commit()
    conn.close()

def save_experiment(config_name, params, results, status='done'):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO experiments (config_name, params, human, likely_human, likely_ai, ai, timestamp, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        config_name,
        json.dumps(params),
        results.get('human', 0),
        results.get('likely_human', 0),
        results.get('likely_ai', 0),
        results.get('ai', 0),
        datetime.now().isoformat(),
        status
    ))
    conn.commit()
    conn.close()

def get_all_experiments():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT * FROM experiments ORDER BY id')
    rows = c.fetchall()
    conn.close()
    return rows

def set_state(key, value):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('REPLACE INTO experiment_state (key, value) VALUES (?, ?)', (key, value))
    conn.commit()
    conn.close()

def get_state(key):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT value FROM experiment_state WHERE key = ?', (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None
