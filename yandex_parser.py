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
        time.sleep(3)
        
        # Сохраняем начальный скриншот
        driver.save_screenshot('/tmp/yandex_start.png')
        
        # Поле ввода
        logger.info("Ожидаем поле ввода...")
        textarea = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'textarea[placeholder*="текст"], textarea[placeholder*="Текст"]'))
        )
        textarea.clear()
        textarea.send_keys(text)
        logger.info(f"Текст вставлен, длина: {len(text)} символов")
        time.sleep(1)
        
        # Кнопка
        submit_btn = None
        selectors = [
            (By.ID, "analyze-btn"),
            (By.CSS_SELECTOR, "button[type='submit']"),
            (By.XPATH, "//button[contains(translate(text(), 'А-Яа-я', 'a-za-z'), 'проверить')]"),
            (By.XPATH, "//button[contains(@class, 'button') and contains(text(), 'Проверить')]"),
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
            driver.save_screenshot('/tmp/yandex_no_button.png')
            raise Exception("Кнопка не найдена")
        
        logger.info("Нажимаем кнопку 'Проверить'...")
        submit_btn.click()
        
        # Ожидаем появления результатов – используем разные признаки
        logger.info("Ожидаем результаты анализа...")
        # Ждём появления любых элементов, указывающих на завершение
        try:
            WebDriverWait(driver, 45).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, '.segment-item, .analysis-results, .distribution, .result-block, .progress-done'))
            )
        except TimeoutException:
            # Возможно, результаты уже есть, но селектор не тот – попробуем сохранить и проверить
            pass
        
        time.sleep(3)  # даём время дорисовать
        
        # Сохраняем скриншот после анализа
        driver.save_screenshot('/tmp/yandex_result.png')
        html = driver.page_source
        with open('/tmp/yandex_result.html', 'w', encoding='utf-8') as f:
            f.write(html)
        
        # Парсим распределение – ищем цифры с процентами в специальных блоках
        result = {'human': 0, 'likely_human': 0, 'likely_ai': 0, 'ai': 0}
        
        # 1. Ищем элементы с классом distribution-value
        blocks = driver.find_elements(By.CSS_SELECTOR, '.distribution-value')
        if blocks:
            values = [int(b.text.replace('%', '')) for b in blocks if b.text and b.text.replace('%', '').isdigit()]
            if len(values) == 4:
                result['ai'] = values[0]
                result['likely_ai'] = values[1]
                result['likely_human'] = values[2]
                result['human'] = values[3]
                logger.info(f"Распределение найдено (distribution-value): {result}")
                return result
            else:
                logger.warning(f"Найдено distribution-value, но не 4 значения: {values}")
        
        # 2. Ищем элементы с классами, содержащими 'percent' или 'value'
        numbers = driver.find_elements(By.CSS_SELECTOR, '.percent, .value, .stat-value, .number')
        values = []
        for el in numbers:
            txt = el.text.replace('%', '').strip()
            if txt and txt.isdigit():
                values.append(int(txt))
        if len(values) >= 4:
            # Берём последние 4 (предполагаем порядок AI, Likely AI, Likely Human, Human)
            vals = values[-4:]
            result['ai'] = vals[0]
            result['likely_ai'] = vals[1]
            result['likely_human'] = vals[2]
            result['human'] = vals[3]
            logger.info(f"Распределение найдено (по numbers): {result}")
            return result
        else:
            logger.warning(f"Найдено numbers: {values}, но недостаточно")
        
        # 3. Ищем все числа с процентами на странице (любые)
        import re
        text_content = driver.find_element(By.TAG_NAME, 'body').text
        # Ищем числа с процентами (например, "43%")
        matches = re.findall(r'(\d+)%', text_content)
        if matches:
            nums = [int(m) for m in matches if 0 <= int(m) <= 100]
            # Берём 4 наиболее вероятных (первые 4 или последние 4)
            if len(nums) >= 4:
                vals = nums[-4:]  # часто порядок AI, Likely AI, Likely Human, Human
                result['ai'] = vals[0] if len(vals)>0 else 0
                result['likely_ai'] = vals[1] if len(vals)>1 else 0
                result['likely_human'] = vals[2] if len(vals)>2 else 0
                result['human'] = vals[3] if len(vals)>3 else 0
                logger.info(f"Распределение найдено по regex (первые 4 числа): {result}")
                return result
        
        # Если ничего не найдено, сохраняем HTML для отладки и возвращаем нули
        logger.error("Не удалось найти распределение. HTML сохранён в /tmp/yandex_result.html")
        return result
        
    except Exception as e:
        logger.exception(f"Ошибка при парсинге Яндекса: {e}")
        try:
            driver.save_screenshot('/tmp/yandex_error.png')
            with open('/tmp/yandex_error.html', 'w', encoding='utf-8') as f:
                f.write(driver.page_source)
        except:
            pass
        raise
    finally:
        driver.quit()
