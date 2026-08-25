"""
config.py — настройки пост-обработки для Chapter Editor
Здесь можно быстро менять вероятности и словари.
"""

# ========== Случайный seed (для воспроизводимости) ==========
RANDOM_SEED = 42

# ========== Вероятности операций (0.0 - 1.0) ==========
PROB_SYNONYMS = 0.6
PROB_INSERTIONS = 0.3
PROB_SWAP_FIRST_WORDS = 0.25
PROB_SWAP_CLAUSES = 0.6
PROB_DIRECT_INDIRECT = 0.45
PROB_INVERSION = 0.25
PROB_INTERJECTIONS = 0.2
PROB_PARTICLES = 0.15
PROB_SWAP_SUBJECT_PREDICATE = 0.15

# ========== Словари для замен ==========
SYNONYMS_DICT = { ... }  # (содержимое как выше, не изменяем)
INSERTIONS_LIST = ['впрочем', 'кстати', ...]
INTERJECTIONS_LIST = ['ах', 'ой', ...]
PARTICLES_LIST = ['же', 'ведь', ...]
ADVERBS_LIST = ['вчера', 'сегодня', ...]
REPORTING_VERBS = ['сказал', 'сказала', ...]
CLAUSE_CONJUNCTIONS = ['когда', 'если', ...]
