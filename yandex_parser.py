# yandex_parser.py
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service

def parse_yandex_neuro(text):
    """
    Открывает страницу, вставляет текст, ждёт результат,
    возвращает словарь с ключами: human, likely_human, likely_ai, ai (в процентах)
    """
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,1080')
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    try:
        driver.get('https://yandex.ru/lab/neurodetector')
        # Ждём загрузки поля ввода
        textarea = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'textarea[placeholder*="текст"]'))
        )
        textarea.clear()
        textarea.send_keys(text)
        
        # Нажимаем кнопку "Проверить"
        submit_btn = driver.find_element(By.XPATH, '//button[contains(text(), "Проверить")]')
        submit_btn.click()
        
        # Ждём появления результатов (можно искать сегменты)
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '.segment-item'))
        )
        time.sleep(2)  # дополнительная задержка для полного обновления
        
        # Парсим итоговые проценты (они могут быть в специальном блоке)
        # Пример: <span class="distribution__value">43%</span>
        # Ищем все элементы с классами, содержащими "distribution"
        distribution = driver.find_elements(By.CSS_SELECTOR, '.distribution__item')
        # Обычно порядок: AI, LIKELY_AI, LIKELY_HUMAN, HUMAN (но может меняться)
        # Лучше искать по тексту метки
        labels = ['AI', 'LIKELY_AI', 'LIKELY_HUMAN', 'HUMAN']
        result = {}
        for label in labels:
            try:
                elem = driver.find_element(By.XPATH, f'//span[contains(text(), "{label}")]/../span[contains(@class, "value")]')
                value = int(elem.text.replace('%', ''))
                result[label.lower()] = value
            except:
                result[label.lower()] = 0
        # Если не нашли, попробуем альтернативный селектор
        if not result or sum(result.values()) == 0:
            # Ищем все цифры в блоке результатов
            blocks = driver.find_elements(By.CSS_SELECTOR, '.distribution-value')
            values = [int(b.text.replace('%', '')) for b in blocks if b.text]
            if len(values) == 4:
                result = {
                    'ai': values[0],
                    'likely_ai': values[1],
                    'likely_human': values[2],
                    'human': values[3]
                }
        return result
    finally:
        driver.quit()
