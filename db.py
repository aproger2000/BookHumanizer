# db.py
import sqlite3
import json
import logging
from datetime import datetime

DB_PATH = 'experiments.db'

# Настройка логгера (если не настроен глобально)
logger = logging.getLogger(__name__)

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
    logger.info("Database initialized")

def save_experiment(config_name, params, results, status='done'):
    """
    Сохраняет эксперимент в БД с полным логированием.
    """
    logger.info(f"SAVE_EXPERIMENT called: {config_name}, params: {params}, results: {results}, status: {status}")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
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
    """Заполняет таблицу экспериментов историческими данными, если она пуста."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM experiments')
    count = c.fetchone()[0]
    if count > 0:
        conn.close()
        logger.info("Таблица экспериментов уже содержит данные, пропускаем seed.")
        return

    history = [
        ('v1.18 (база)', {'PROB_SYNONYMS': 0.3, 'PROB_TYPOS': 0.3, 'PROB_PARTICLES': 0.25, 'PROB_INTERJECTIONS': 0.25, 'PROB_SWAP_FIRST_WORDS': 0.3}, 50, 0, 0, 50, 'done'),
        ('v1.24 (синтаксис)', {'PROB_SWAP_CLAUSES': 0.2, 'PROB_DIRECT_INDIRECT': 0.15}, 43, 5, 10, 42, 'done'),
        ('v1.25 (синонимы 0.5)', {'PROB_SYNONYMS': 0.5}, 43, 5, 10, 42, 'done'),
        ('v1.16 (междометия)', {'PROB_INTERJECTIONS': 0.25}, 29, 21, 14, 36, 'done'),
        ('v1.15 (удаление маркеров)', {'PROB_REMOVE_AI_MARKERS': 0.7}, 29, 7, 14, 50, 'done'),
        ('v1.12 (частицы)', {'PROB_PARTICLES': 0.25}, 38, 0, 0, 62, 'done'),
        ('v1.7 (опечатки)', {'PROB_TYPOS': 0.3}, 36, 7, 14, 43, 'done'),
        ('v1.22 (перестановка)', {'PROB_SWAP_FIRST_WORDS': 0.4}, 50, 0, 0, 50, 'done'),
        ('v1.19 (разбивка)', {'PROB_SPLIT_LONG_SENTENCES': 0.4}, 21, 0, 0, 79, 'done'),
    ]
    for config_name, params, human, likely_human, likely_ai, ai, status in history:
        c.execute('''
            INSERT INTO experiments (config_name, params, human, likely_human, likely_ai, ai, timestamp, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            config_name,
            json.dumps(params),
            human,
            likely_human,
            likely_ai,
            ai,
            datetime.now().isoformat(),
            status
        ))
    conn.commit()
    conn.close()
    logger.info(f"Добавлено {len(history)} исторических экспериментов в БД.")
