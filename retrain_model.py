"""
retrain_model.py — переобучение модели HUMAN
"""
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error
import joblib
import sys
from app import extract_features

def train():
    print("=== RETRAIN START ===")
    try:
        df = pd.read_csv('training_data.csv')
    except Exception as e:
        print(f"Ошибка чтения training_data.csv: {e}")
        return

    df = df.dropna(subset=['HUMAN_yandex'])
    if len(df) == 0:
        print("Нет данных для обучения. Пропускаем.")
        return

    print(f"Найдено {len(df)} записей.")

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

    feature_names = list(feature_rows[0].keys())
    X = pd.DataFrame(feature_rows)[feature_names]
    y = df['HUMAN_yandex'].values[:len(X)]

    if len(X) < 5:
        print("Слишком мало примеров (<5). Пропускаем.")
        return

    print(f"Обучение на {len(X)} примерах...")
    model = RandomForestRegressor(n_estimators=100, random_state=42, n_jobs=-1)
    model.fit(X, y)

    y_pred = model.predict(X)
    mae = mean_absolute_error(y, y_pred)
    print(f"MAE на обучении: {mae:.2f}")

    joblib.dump(model, 'human_model.pkl')
    with open('feature_cols.txt', 'w') as f:
        f.write(','.join(feature_names))
    print("Модель сохранена.")
    print("=== RETRAIN END ===")

if __name__ == "__main__":
    train()
