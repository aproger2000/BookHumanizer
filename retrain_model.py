"""
retrain_model.py — переобучение модели HUMAN на основе новых данных
Запускается вручную или через cron-задачу.
"""
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, r2_score
import joblib
import re
import sys

# Импортируем функцию из app.py для извлечения признаков
# (если app.py не загружается, можно продублировать функцию)
from app import extract_features

def train():
    print("Загрузка данных из training_data.csv...")
    df = pd.read_csv('training_data.csv')
    # Удаляем строки без HUMAN_yandex
    df = df.dropna(subset=['HUMAN_yandex'])
    if len(df) == 0:
        print("Нет данных для обучения. Пропускаем.")
        return

    # Извлекаем признаки из processed_text
    print("Извлечение признаков...")
    feature_rows = []
    for idx, row in df.iterrows():
        text = row['processed_text']
        if not isinstance(text, str) or len(text) < 10:
            continue
        feats = extract_features(text)
        feature_rows.append(feats)
    if not feature_rows:
        print("Не удалось извлечь признаки. Пропускаем.")
        return

    # Определяем список признаков (порядок важен)
    # Берём все ключи из первого словаря
    feature_names = list(feature_rows[0].keys())
    X = pd.DataFrame(feature_rows)[feature_names]
    y = df['HUMAN_yandex'].values[:len(X)]  # убедимся, что длины совпадают

    if len(X) < 5:
        print("Слишком мало примеров (<5). Пропускаем.")
        return

    # Обучаем модель
    print(f"Обучение на {len(X)} примерах...")
    model = RandomForestRegressor(n_estimators=100, random_state=42, n_jobs=-1)
    model.fit(X, y)

    # Оценка (на всех данных для информации)
    y_pred = model.predict(X)
    mae = mean_absolute_error(y, y_pred)
    print(f"MAE на обучении: {mae:.2f}")

    # Сохраняем модель и список признаков
    joblib.dump(model, 'human_model.pkl')
    with open('feature_cols.txt', 'w') as f:
        f.write(','.join(feature_names))
    print("Модель сохранена.")

if __name__ == "__main__":
    train()
