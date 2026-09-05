# yandex_parser.py
import os
import time
import logging
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException

logger = logging.getLogger(__name__)

def parse_yandex_neuro(text):
    """
    Парсит Яндекс.Нейродетектор и возвращает проценты.
    При ошибке логирует и выбрасывает исключение.
    """
    if len(text) < 150:
        raise ValueError("Текст слишком короткий для анализа (мин. 150 символов)")

    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option('excludeSwitches', ['enable-automation'])
    options.add_experimental_option('useAutomationExtension', False)
    
    # User-Agent для эмуляции обычного браузера
    options.add_argument('user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36')
    
    chrome_bin = os.environ.get('CHROME_BIN', './chrome/chrome-linux64/chrome')
    if os.path.exists(chrome_bin):
        options.binary_location = chrome_bin
        logger.info(f"Используем Chrome из: {chrome_bin}")
    else:
        logger.warning(f"Chrome не найден по пути {chrome_bin}, используется системный")
    
    chromedriver_path = os.environ.get('CHROMEDRIVER_PATH', './chromedriver-linux64/chromedriver')
    if os.path.exists(chromedriver_path):
        service = Service(executable_path=chromedriver_path)
        logger.info(f"Используем chromedriver из: {chromedriver_path}")
    else:
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())
        logger.info("Используем chromedriver из webdriver-manager")
    
    driver = webdriver.Chrome(service=service, options=options)
    try:
        logger.info("Загружаем страницу Яндекс.Нейродетектора...")
        driver.get('https://yandex.ru/lab/neurodetector')
        time.sleep(2)  # ждём начальной загрузки
        
        # Сохраняем скриншот для отладки (если упадёт)
        try:
            driver.save_screenshot('/tmp/yandex_start.png')
        except:
            pass
        
        # Ожидаем появления текстовой области
        logger.info("Ожидаем поле ввода...")
        textarea = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'textarea[placeholder*="текст"], textarea[placeholder*="Текст"]'))
        )
        textarea.clear()
        textarea.send_keys(text)
        logger.info(f"Текст вставлен, длина: {len(text)} символов")
        time.sleep(1)  # даём время на обновление
        
        # Множественные стратегии поиска кнопки
        submit_btn = None
        selectors = [
            (By.ID, "analyze-btn"),
            (By.CSS_SELECTOR, "button[type='submit']"),
            (By.XPATH, "//button[contains(translate(text(), 'А-Яа-я', 'a-za-z'), 'проверить')]"),
            (By.XPATH, "//button[contains(@class, 'button') and contains(text(), 'Проверить')]"),
            (By.XPATH, "//button[contains(@class, 'button') and contains(text(), 'ПРОВЕРИТЬ')]"),
            (By.XPATH, "//button[contains(@class, 'button')]"),
            (By.CSS_SELECTOR, ".button"),
            (By.CSS_SELECTOR, "button"),
        ]
        
        logger.info("Ищем кнопку 'Проверить'...")
        for by, selector in selectors:
            try:
                submit_btn = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((by, selector))
                )
                logger.info(f"Кнопка найдена по селектору: {by} -> {selector}")
                break
            except TimeoutException:
                continue
        
        if not submit_btn:
            # Сохраняем HTML для анализа
            html = driver.page_source
            with open('/tmp/yandex_page.html', 'w', encoding='utf-8') as f:
                f.write(html)
            driver.save_screenshot('/tmp/yandex_fail.png')
            raise Exception("Кнопка 'Проверить' не найдена ни одним селектором. HTML сохранён в /tmp/yandex_page.html")
        
        logger.info("Нажимаем кнопку 'Проверить'...")
        submit_btn.click()
        
        # Ожидаем появления сегментов результатов
        logger.info("Ожидаем результаты анализа...")
        WebDriverWait(driver, 45).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '.segment-item, .analysis-results, .distribution'))
        )
        time.sleep(2)  # даём время на полное обновление
        
        # Парсим распределение
        result = {'human': 0, 'likely_human': 0, 'likely_ai': 0, 'ai': 0}
        
        # Сначала ищем блоки с цифрами
        blocks = driver.find_elements(By.CSS_SELECTOR, '.distribution-value')
        if blocks:
            values = [int(b.text.replace('%', '')) for b in blocks if b.text]
            if len(values) == 4:
                result['ai'] = values[0]
                result['likely_ai'] = values[1]
                result['likely_human'] = values[2]
                result['human'] = values[3]
                logger.info(f"Распределение найдено: {result}")
            else:
                logger.warning(f"Найдено {len(values)} значений, ожидалось 4")
        else:
            # Альтернативный парсинг: ищем все числа в блоке результатов
            numbers = driver.find_elements(By.CSS_SELECTOR, '.distribution-item .number, .stat-value')
            if numbers:
                values = [int(n.text.replace('%', '')) for n in numbers if n.text]
                if len(values) == 4:
                    result['ai'] = values[0]
                    result['likely_ai'] = values[1]
                    result['likely_human'] = values[2]
                    result['human'] = values[3]
                    logger.info(f"Распределение (альт.) найдено: {result}")
            else:
                # Попробуем найти сегменты и посчитать проценты вручную
                segments = driver.find_elements(By.CSS_SELECTOR, '.segment-item')
                if segments:
                    # Здесь можно реализовать подсчёт, но пока оставим заглушку
                    logger.warning("Не удалось найти распределение, но сегменты есть")
        
        return result
        
    except Exception as e:
        logger.exception(f"Ошибка при парсинге Яндекса: {e}")
        try:
            driver.save_screenshot('/tmp/yandex_error.png')
            with open('/tmp/yandex_error.html', 'w', encoding='utf-8') as f:
                f.write(driver.page_source)
            logger.info("Скриншот и HTML сохранены в /tmp/ для отладки")
        except:
            pass
        raise
    finally:
        driver.quit()
