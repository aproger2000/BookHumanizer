# experiment_runner.py
import os
import sys
import json
import random
import requests
from db import init_db, save_experiment, get_state, set_state
from yandex_parser import parse_yandex_neuro
import config as base_config

# Путь к тестовому тексту (можно загружать один раз)
TEST_TEXT = "За восемь лет до «Стеклянного Ливня» ... (полный текст)"
# Для реальной работы лучше скачать с URL: https://author.today/reader/625253/5975029

# Список параметров для оптимизации (имя в config, начальное значение, мин, макс, шаг)
PARAMS = [
    ('PROB_SYNONYMS', 0.3, 0.3, 0.7, 0.05),
    ('PROB_TYPOS', 0.3, 0.2, 0.5, 0.05),
    ('PROB_PARTICLES', 0.25, 0.15, 0.4, 0.05),
    ('PROB_INTERJECTIONS', 0.25, 0.15, 0.4, 0.05),
    ('PROB_SWAP_FIRST_WORDS', 0.3, 0.2, 0.5, 0.05),
    ('PROB_INSERTIONS', 0.3, 0.2, 0.5, 0.05),
    ('PROB_ADD_COLLOQUIAL', 0.3, 0.2, 0.5, 0.05),
]

def update_config(param_name, value):
    """Обновляет config.py (или загружает динамически)"""
    # Простейший способ: записать в файл конфига, но можно использовать глобальный словарь.
    # Для безопасности создадим временный конфиг и передадим его в запросе к /api/revise.
    # Мы будем передавать параметры в теле запроса, а не менять файл.
    pass

def run_experiment(param_name, new_value):
    # 1. Формируем словарь параметров (копия base_config, но с изменённым)
    params = {
        'PROB_SYNONYMS': base_config.PROB_SYNONYMS,
        'PROB_INSERTIONS': base_config.PROB_INSERTIONS,
        'PROB_SWAP_FIRST_WORDS': base_config.PROB_SWAP_FIRST_WORDS,
        'PROB_INTERJECTIONS': base_config.PROB_INTERJECTIONS,
        'PROB_PARTICLES': base_config.PROB_PARTICLES,
        'PROB_CANCEL_CANCEL': base_config.PROB_CANCEL_CANCEL,
        'PROB_REMOVE_AI_MARKERS': base_config.PROB_REMOVE_AI_MARKERS,
        'PROB_SPLIT_LONG_SENTENCES': base_config.PROB_SPLIT_LONG_SENTENCES,
        'PROB_ADD_COLLOQUIAL': base_config.PROB_ADD_COLLOQUIAL,
        'PROB_TYPOS': base_config.PROB_TYPOS,
        'PROB_SWAP_CLAUSES': 0.0,
        'PROB_DIRECT_INDIRECT': 0.0,
    }
    params[param_name] = new_value

    # 2. Отправляем текст на обработку (через внутренний API)
    response = requests.post('http://localhost:8000/api/revise_internal', json={
        'text': TEST_TEXT,
        'params': params,
        'style': 'neutral'
    })
    if response.status_code != 200:
        raise Exception('Revise failed')
    data = response.json()
    processed_text = data['revised_text']

    # 3. Получаем оценку Яндекса
    yandex_result = parse_yandex_neuro(processed_text)
    # {'human': 43, 'likely_human': 5, 'likely_ai': 10, 'ai': 42}

    # 4. Сохраняем эксперимент
    save_experiment(
        config_name=f"auto_{param_name}_{new_value:.2f}",
        params=params,
        results=yandex_result,
        status='done'
    )

    # 5. Добавляем в training_data.csv для дообучения локального детектора
    # Используем существующий эндпоинт /api/feedback
    feedback_resp = requests.post('http://localhost:8000/api/feedback', json={
        'revised_text': processed_text,
        'yandex_score': yandex_result.get('human', 0)
    })
    # ignore response

    return yandex_result

def main():
    init_db()
    # Получаем состояние: текущий индекс параметра и его значение
    current_idx = int(get_state('current_idx') or 0)
    current_value = float(get_state('current_value') or PARAMS[current_idx][1])
    best_value = float(get_state('best_value') or current_value)
    best_score = float(get_state('best_score') or 0)

    # Проверяем, не достигли ли цели
    if best_score >= 70:
        print("Цель достигнута! Останавливаемся.")
        return

    param_name, base_val, min_val, max_val, step = PARAMS[current_idx]
    # Пробуем увеличить на шаг
    new_value = current_value + step
    if new_value > max_val:
        # Переходим к следующему параметру
        current_idx = (current_idx + 1) % len(PARAMS)
        new_value = PARAMS[current_idx][1]  # начальное значение
        set_state('current_idx', str(current_idx))
        set_state('current_value', str(new_value))
        set_state('best_value', str(new_value))
        set_state('best_score', str(best_score))
        print(f"Переход к параметру {PARAMS[current_idx][0]}")
        return

    # Запускаем эксперимент
    print(f"Запуск: {param_name} = {new_value:.2f}")
    try:
        result = run_experiment(param_name, new_value)
        score = result.get('human', 0) + result.get('likely_human', 0)
        print(f"Результат: HUMAN={result.get('human')}%, LIKELY_HUMAN={result.get('likely_human')}%, сумма={score}%")

        # Сравниваем с лучшим для этого параметра
        if score > best_score:
            # Улучшение – сохраняем новое значение как лучшее
            set_state('best_value', str(new_value))
            set_state('best_score', str(score))
            # Продолжаем увеличивать этот же параметр
            set_state('current_value', str(new_value))
        else:
            # Ухудшение – откатываем к предыдущему лучшему значению и переходим к следующему параметру
            set_state('current_value', str(best_value))  # фактически остаёмся на лучшем
            # Переходим к следующему параметру
            current_idx = (current_idx + 1) % len(PARAMS)
            set_state('current_idx', str(current_idx))
            new_val = PARAMS[current_idx][1]
            set_state('current_value', str(new_val))
            set_state('best_value', str(new_val))
            set_state('best_score', str(best_score))
            print(f"Откат, переходим к {PARAMS[current_idx][0]}")
    except Exception as e:
        print(f"Ошибка: {e}")
        set_state('current_value', str(best_value))  # откат

if __name__ == '__main__':
    main()
