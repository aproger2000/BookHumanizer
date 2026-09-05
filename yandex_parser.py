# yandex_parser.py
import os
import time
import re
import logging
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException

logger = logging.getLogger(__name__)

def parse_yandex_neuro(text):
    if len(text) < 150:
        raise ValueError("Текст слишком короткий (мин. 150 символов)")

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
        time.sleep(2)
        
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
        
        # Ждём появления чисел с процентами (проверяем каждые 2 секунды, до 45 секунд)
        logger.info("Ожидаем появления чисел с % в тексте страницы...")
        found_numbers = None
        for _ in range(45):  # 45 * 2 = 90 секунд
            time.sleep(2)
            body_text = driver.find_element(By.TAG_NAME, 'body').text
            matches = re.findall(r'(\d+)%', body_text)
            if matches and len(matches) >= 4:
                found_numbers = [int(m) for m in matches]
                logger.info(f"Найдены числа: {found_numbers}")
                break
            # Также проверяем элементы с классом distribution
            blocks = driver.find_elements(By.CSS_SELECTOR, '.distribution-value, .percent, .value, .stat-value, .number')
            values = []
            for el in blocks:
                txt = el.text.replace('%', '').strip()
                if txt and txt.isdigit():
                    values.append(int(txt))
            if len(values) >= 4:
                found_numbers = values
                logger.info(f"Найдены числа по блокам: {found_numbers}")
                break
        
        # Сохраняем скриншот и HTML
        driver.save_screenshot('/tmp/yandex_result.png')
        with open('/tmp/yandex_result.html', 'w', encoding='utf-8') as f:
            f.write(driver.page_source)
        logger.info("Скриншот и HTML сохранены в /tmp/")
        
        if found_numbers and len(found_numbers) >= 4:
            vals = found_numbers[-4:]
            result = {
                'ai': vals[0],
                'likely_ai': vals[1],
                'likely_human': vals[2],
                'human': vals[3]
            }
            logger.info(f"Распределение: {result}")
            return result
        else:
            # Дополнительная попытка: ищем все цифры с % в HTML через JavaScript
            script = "return document.body.innerHTML.match(/\\d+%/g);"
            raw = driver.execute_script(script)
            if raw:
                nums = [int(m.replace('%', '')) for m in raw if m.endswith('%')]
                if len(nums) >= 4:
                    vals = nums[-4:]
                    result = {
                        'ai': vals[0],
                        'likely_ai': vals[1],
                        'likely_human': vals[2],
                        'human': vals[3]
                    }
                    logger.info(f"Распределение (JS): {result}")
                    return result
            
            logger.error("Не удалось найти распределение. Возвращаем нули.")
            return {'human': 0, 'likely_human': 0, 'likely_ai': 0, 'ai': 0}
        
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
