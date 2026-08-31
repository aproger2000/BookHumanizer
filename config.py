"""
config.py — настройки пост-обработки и модели ruT5
"""

# ========== Случайный seed ==========
RANDOM_SEED = 42

# ========== Вероятности операций пост-обработки ==========
PROB_SYNONYMS = 0.3
PROB_INSERTIONS = 0.3
PROB_SWAP_FIRST_WORDS = 0.25
PROB_INTERJECTIONS = 0.2
PROB_SWAP_CLAUSES = 0.6
PROB_DIRECT_INDIRECT = 0.45
PROB_INVERSION = 0.25
PROB_SWAP_SUBJECT_PREDICATE = 0.15
PROB_PARTICLES = 0.15

PROB_CANCEL_CANCEL = 0.4
PROB_REMOVE_AI_MARKERS = 0.5
PROB_SPLIT_LONG_SENTENCES = 0.3
PROB_ADD_COLLOQUIAL = 0.3
PROB_CHANGE_WORD_ORDER = 0.2
PROB_TYPOS = 0.15

# ========== Настройки модели ruT5 ==========
# Включить использование ruT5 для глубокого перефразирования
USE_RU_T5 = True
# Минимальная длина абзаца для применения модели (символы)
MIN_PARAGRAPH_LENGTH = 30
# Порог HUMAN, при котором применяется модель (если HUMAN < THRESHOLD)
RU_T5_THRESHOLD = 50
# Количество попыток генерации (для выбора лучшего)
RU_T5_ATTEMPTS = 2
# Температура для разнообразия
RU_T5_TEMPERATURE = 1.0

# ========== Словари (без изменений) ==========
# ... (все словари, которые были ранее, остаются без изменений)
