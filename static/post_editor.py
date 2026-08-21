"""
post_editor.py — универсальный модуль коррекции артефактов на основе сравнения с оригиналом.
"""
import re
import logging
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

# Список явных маркеров артефактов (регулярные выражения)
ARTIFACT_MARKERS = [
    r'I thought so',
    r'They will all call',
    r'No bureaucracy',
    r'No grant fees',
    r'In return nothing',
    r'It\'s just that',
    r'from the beginning',
    r'Alexey remained silent',
    r'Cross continued',
    r'funds\?',
    r'like a shark',
    r'glass like a',
    r'MIT',
    r'silent\.',
    r'empty null',
    r'vino quieren',
    r'Laboratorio, presupuesto',
    r'Equipment\. Todo lo quieras',
    r'shark\'s',
    r'null vacuum',
    r'final drawings',
    r'lo voy a pensar',
]


def is_artifact(text: str) -> bool:
    """Определяет, является ли текст артефактным."""
    if not text:
        return False

    # Проверка на маркеры
    for marker in ARTIFACT_MARKERS:
        if re.search(marker, text, flags=re.IGNORECASE):
            return True

    # Доля латиницы (букв)
    letters = sum(1 for ch in text if ch.isalpha())
    if letters == 0:
        return False
    latin_count = sum(1 for ch in text if 'a' <= ch.lower() <= 'z')
    latin_ratio = latin_count / letters

    # Если >30% латиницы — артефакт
    if latin_ratio > 0.3:
        return True

    return False


def similarity(a: str, b: str) -> float:
    """Сравнивает два текста с помощью difflib."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def find_best_match(artifact_paragraph: str, original_paragraphs: list) -> str:
    """
    Ищет в оригинальных абзацах наиболее похожий по длине и содержанию.
    Возвращает оригинальный абзац, если найден, иначе исходный (без изменений).
    """
    if not original_paragraphs:
        return artifact_paragraph

    best_match = None
    best_score = 0.0

    # Извлекаем ключевые слова из артефактного абзаца (имена, числа, термины)
    keywords = set(re.findall(r'\b[А-ЯA-Z][а-яa-z]*\b', artifact_paragraph))

    for para in original_paragraphs:
        # Сначала сравниваем по набору ключевых слов
        para_keywords = set(re.findall(r'\b[А-ЯA-Z][а-яa-z]*\b', para))
        if not keywords or not para_keywords:
            continue
        keyword_overlap = len(keywords & para_keywords) / max(len(keywords), len(para_keywords))
        if keyword_overlap > 0.5:
            # Дополнительно проверяем общую схожесть
            score = similarity(artifact_paragraph, para)
            if score > best_score:
                best_score = score
                best_match = para

    # Если не нашли по ключевым словам, ищем по общей схожести
    if best_match is None:
        for para in original_paragraphs:
            score = similarity(artifact_paragraph, para)
            if score > best_score:
                best_score = score
                best_match = para

    # Если схожесть >0.3, считаем замену допустимой, иначе оставляем артефакт
    if best_score > 0.3 and best_match:
        logger.info(f"Replacing artifact with original: '{best_match[:50]}...'")
        return best_match
    else:
        return artifact_paragraph


def repair_artifacts(original_text: str, translated_text: str) -> str:
    """
    Основная функция: исправляет артефакты в переведённом тексте,
    заменяя их на соответствующие абзацы из оригинала.
    """
    if not original_text or not translated_text:
        return translated_text

    # Разбиваем на абзацы
    original_paragraphs = original_text.split('\n\n')
    translated_paragraphs = translated_text.split('\n\n')

    # Если количество абзацев совпадает, обрабатываем попарно
    if len(translated_paragraphs) == len(original_paragraphs):
        result = []
        for i, para in enumerate(translated_paragraphs):
            if is_artifact(para):
                # Заменяем на оригинал, если он есть
                fixed = find_best_match(para, [original_paragraphs[i]])
                result.append(fixed)
            else:
                result.append(para)
        return '\n\n'.join(result)

    # Если абзацев разное количество, ищем глобально для каждого артефактного абзаца
    result = []
    for para in translated_paragraphs:
        if is_artifact(para):
            fixed = find_best_match(para, original_paragraphs)
            result.append(fixed)
        else:
            result.append(para)
    return '\n\n'.join(result)
