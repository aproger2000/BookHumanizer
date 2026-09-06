import os
import sqlite3
import json
import logging
from datetime import datetime

# Определяем абсолютный путь к БД в папке проекта (которая должна быть примонтирована через Persistent Disk)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'experiments.db')

logger = logging.getLogger(__name__)

def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    # Включаем WAL режим для многопоточности
    conn.execute('PRAGMA journal_mode=WAL')
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
    logger.info(f"Database initialized at {DB_PATH} (WAL mode)")

def save_experiment(config_name, params, results, status='done', revised_text=''):
    logger.info(f"SAVE_EXPERIMENT called: {config_name}, DB_PATH={DB_PATH}")
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    try:
        # Проверяем количество записей до вставки
        c.execute('SELECT COUNT(*) FROM experiments')
        count_before = c.fetchone()[0]
        logger.info(f"Count before insert: {count_before}")

        c.execute('''
            INSERT INTO experiments (config_name, params, human, likely_human, likely_ai, ai, timestamp, status, revised_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            config_name,
            json.dumps(params, ensure_ascii=False),
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
        # Проверяем, что запись действительно появилась
        c.execute('SELECT COUNT(*) FROM experiments')
        count_after = c.fetchone()[0]
        logger.info(f"Count after insert: {count_after}, lastrowid: {last_id}")
        if count_after == count_before:
            logger.error("Count did not increase! Rollback may have occurred.")
        else:
            # Дополнительная проверка: читаем запись по id
            c.execute('SELECT id, config_name FROM experiments WHERE id = ?', (last_id,))
            row = c.fetchone()
            if row:
                logger.info(f"Verified: record with id {row[0]} exists: {row[1]}")
            else:
                logger.error(f"Record with id {last_id} not found after commit!")
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
