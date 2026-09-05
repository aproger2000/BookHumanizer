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
        raise ValueError("Текст слишком короткий")

    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--disable-gpu')
    options.add_argument('user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36')

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
        time.sleep(2)

        textarea = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'textarea[placeholder*="текст"]'))
        )
        textarea.clear()
        textarea.send_keys(text)
        time.sleep(1)

        submit_btn = None
        for by, selector in [
            (By.ID, "analyze-btn"),
            (By.CSS_SELECTOR, "button[type='submit']"),
            (By.XPATH, "//button[contains(text(), 'Проверить')]"),
        ]:
            try:
                submit_btn = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((by, selector))
                )
                break
            except:
                continue

        if not submit_btn:
            raise Exception("Кнопка не найдена")

        submit_btn.click()
        time.sleep(30)  # даём время на анализ

        # Сохраняем результат для отладки
        driver.save_screenshot('/tmp/yandex_result.png')
        with open('/tmp/yandex_result.html', 'w', encoding='utf-8') as f:
            f.write(driver.page_source)

        # Ищем все числа с % в тексте страницы
        body = driver.find_element(By.TAG_NAME, 'body').text
        nums = re.findall(r'(\d+)%', body)
        if len(nums) >= 4:
            vals = [int(n) for n in nums[-4:]]
            return {
                'ai': vals[0],
                'likely_ai': vals[1],
                'likely_human': vals[2],
                'human': vals[3]
            }
        else:
            # Попробуем найти в блоках
            blocks = driver.find_elements(By.CSS_SELECTOR, '.distribution-value')
            values = [int(el.text.replace('%', '')) for el in blocks if el.text]
            if len(values) >= 4:
                vals = values[-4:]
                return {
                    'ai': vals[0],
                    'likely_ai': vals[1],
                    'likely_human': vals[2],
                    'human': vals[3]
                }
            raise Exception("Не удалось найти распределение")

    finally:
        driver.quit()
