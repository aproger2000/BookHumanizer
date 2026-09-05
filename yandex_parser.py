# yandex_parser.py
import os
import re
import logging
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

def parse_yandex_neuro(text):
    if len(text) < 150:
        raise ValueError("Текст слишком короткий (мин. 150 символов)")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage']
        )
        context = browser.new_context(
            user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36'
        )
        page = context.new_page()
        try:
            logger.info("Загружаем страницу Яндекс.Нейродетектора...")
            page.goto('https://yandex.ru/lab/neurodetector', timeout=30000)
            page.wait_for_load_state('networkidle')

            # Вставляем текст
            logger.info("Вставляем текст...")
            textarea = page.locator('textarea[placeholder*="текст"]').first
            textarea.fill(text)
            logger.info(f"Текст вставлен, длина: {len(text)} символов")

            # Нажимаем кнопку "Проверить"
            logger.info("Ищем кнопку 'Проверить'...")
            submit_btn = page.locator('button:has-text("Проверить")').first
            if not submit_btn.is_visible():
                # альтернативный селектор
                submit_btn = page.locator('#analyze-btn').first
            submit_btn.click()
            logger.info("Кнопка нажата, ждём результаты...")

            # Ждём появления чисел с процентами
            # Проверяем каждые 2 секунды, максимум 60 секунд
            result = None
            for _ in range(30):
                page.wait_for_timeout(2000)
                # Ищем все числа с % в тексте страницы
                body = page.locator('body').text_content()
                matches = re.findall(r'(\d+)%', body)
                if matches and len(matches) >= 4:
                    vals = [int(m) for m in matches[-4:]]
                    result = {
                        'ai': vals[0],
                        'likely_ai': vals[1],
                        'likely_human': vals[2],
                        'human': vals[3]
                    }
                    logger.info(f"Найдены результаты: {result}")
                    break
                else:
                    # Попробуем поискать в конкретных блоках
                    blocks = page.locator('.distribution-value').all()
                    values = []
                    for el in blocks:
                        txt = el.text_content()
                        if txt and txt.strip().endswith('%'):
                            try:
                                values.append(int(txt.replace('%', '').strip()))
                            except:
                                pass
                    if len(values) >= 4:
                        vals = values[-4:]
                        result = {
                            'ai': vals[0],
                            'likely_ai': vals[1],
                            'likely_human': vals[2],
                            'human': vals[3]
                        }
                        logger.info(f"Найдены результаты (по блокам): {result}")
                        break

            if result is None:
                logger.error("Не удалось найти результаты анализа.")
                # Сохраняем скриншот для отладки
                screenshot_path = '/tmp/yandex_fail.png'
                page.screenshot(path=screenshot_path)
                logger.info(f"Скриншот сохранён: {screenshot_path}")
                raise Exception("Не удалось найти распределение процентов")

            return result
        finally:
            browser.close()
