"""
Модуль локального перефразирования с использованием ruT5-small.
Загружает модель только при первом вызове (ленивая инициализация).
"""
import re
import logging
import os
import random

logger = logging.getLogger(__name__)

# Глобальные переменные для модели
_model = None
_tokenizer = None
_MODEL_LOADED = False


def load_model():
    """Загружает модель ruT5-small (или mt5-small) для перефразирования."""
    global _model, _tokenizer, _MODEL_LOADED
    if _MODEL_LOADED:
        return

    try:
        from transformers import T5ForConditionalGeneration, T5Tokenizer
        import torch

        # Используем компактную модель (~300 МБ)
        model_name = "cointegrated/ruT5-small"  # или "google/mt5-small" для мультиязычного
        logger.info(f"Loading humanizer model: {model_name}...")
        _tokenizer = T5Tokenizer.from_pretrained(model_name)
        _model = T5ForConditionalGeneration.from_pretrained(model_name)
        _model.eval()
        _MODEL_LOADED = True
        logger.info("Humanizer model loaded successfully.")
    except ImportError:
        logger.warning("transformers or torch not installed. Humanizer disabled.")
        _MODEL_LOADED = False
    except Exception as e:
        logger.error(f"Failed to load humanizer model: {e}")
        _MODEL_LOADED = False


def paraphrase_sentence(sentence: str, max_length: int = 128) -> str:
    """
    Перефразирует одно предложение с помощью ruT5.
    Возвращает исходное предложение в случае ошибки.
    """
    if not _MODEL_LOADED or not sentence or len(sentence) < 10:
        return sentence

    try:
        # Формируем промпт для T5 (обычно используется префикс "paraphrase: ")
        input_text = f"paraphrase: {sentence}"
        inputs = _tokenizer(input_text, return_tensors="pt", truncation=True, max_length=max_length)

        # Генерируем с небольшой температурой для разнообразия
        with torch.no_grad():
            outputs = _model.generate(
                **inputs,
                max_length=max_length,
                temperature=0.7,
                do_sample=True,
                top_p=0.9,
                repetition_penalty=1.1
            )
        paraphrased = _tokenizer.decode(outputs[0], skip_special_tokens=True)

        # Если перефразирование не удалось, возвращаем исходное
        if paraphrased and len(paraphrased) > 5:
            return paraphrased
        return sentence
    except Exception as e:
        logger.warning(f"Paraphrasing error: {e}")
        return sentence


def enhance_text(text: str, probability: float = 0.3) -> str:
    """
    Перефразирует случайные предложения в тексте с заданной вероятностью.
    probability: вероятность замены каждого предложения (0.0–1.0).
    """
    if not text or len(text) < 200:
        return text

    # Загружаем модель, если ещё не загружена
    if not _MODEL_LOADED:
        load_model()
        if not _MODEL_LOADED:
            return text  # модель не доступна — возвращаем исходный текст

    # Разбиваем на предложения по .!?
    sentences = re.split(r'(?<=[.!?])\s+', text)
    new_sentences = []
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        # Перефразируем только если предложение длинное (>20 символов) и случайно
        if len(sent) > 20 and random.random() < probability:
            paraphrased = paraphrase_sentence(sent)
            new_sentences.append(paraphrased)
        else:
            new_sentences.append(sent)

    return ' '.join(new_sentences)
