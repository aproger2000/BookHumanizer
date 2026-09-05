# yandex_parser.py
import os
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException

def parse_yandex_neuro(text):
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,1080')
    # Увеличиваем таймаут для загрузки страницы
    options.add_argument('--page-load-strategy=normal')
    
    chrome_bin = os.environ.get('CHROME_BIN', './chrome/chrome-linux64/chrome')
    if os.path.exists(chrome_bin):
        options.binary_location = chrome_bin
    
    chromedriver_path = os.environ.get('CHROMEDRIVER_PATH', './chromedriver-linux64/chromedriver')
    if os.path.exists(chromedriver_path):
        service = Service(executable_path=chromedriver_path)
    else:
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())
    
    driver = webdriver.Chrome(service=service, options=options)
    try:
        driver.get('https://yandex.ru/lab/neurodetector')
        
        # Ожидаем появления текстового поля
        textarea = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'textarea[placeholder*="текст"]'))
        )
        textarea.clear()
        textarea.send_keys(text)
        time.sleep(0.5)  # даём время на обновление интерфейса
        
        # Пробуем найти кнопку разными способами
        submit_btn = None
        selectors = [
            (By.ID, "analyze-btn"),
            (By.XPATH, "//button[contains(translate(text(), 'А-Яа-я', 'a-za-z'), 'проверить')]"),
            (By.XPATH, "//button[contains(@class, 'button') and contains(translate(text(), 'А-Яа-я', 'a-za-z'), 'проверить')]"),
            (By.CSS_SELECTOR, "button[type='submit']"),
            (By.XPATH, "//button[contains(text(), 'Проверить') or contains(text(), 'ПРОВЕРИТЬ')]")
        ]
        
        for by, selector in selectors:
            try:
                submit_btn = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((by, selector))
                )
                if submit_btn:
                    break
            except TimeoutException:
                continue
        
        if not submit_btn:
            raise Exception("Кнопка 'Проверить' не найдена ни одним из селекторов")
        
        submit_btn.click()
        
        # Ожидаем появления результатов
        WebDriverWait(driver, 45).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '.segment-item'))
        )
        time.sleep(2)  # даём время на полное обновление
        
        # Парсим результат
        result = {'human': 0, 'likely_human': 0, 'likely_ai': 0, 'ai': 0}
        blocks = driver.find_elements(By.CSS_SELECTOR, '.distribution-value')
        values = [int(b.text.replace('%', '')) for b in blocks if b.text]
        if len(values) == 4:
            result['ai'] = values[0]
            result['likely_ai'] = values[1]
            result['likely_human'] = values[2]
            result['human'] = values[3]
        else:
            # Fallback: ищем значения в другом месте (например, в сегментах)
            segments = driver.find_elements(By.CSS_SELECTOR, '.segment-item')
            if segments:
                # Можно попытаться парсить проценты из сегментов, но пока оставим
                pass
        
        return result
    finally:
        driver.quit()
