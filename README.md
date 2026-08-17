# Редактор глав

Простой сервис для редакторской правки глав книги, черновик которых
сделан с помощью ИИ. Алгоритм не «маскирует» текст под конкретный
детектор, а выполняет обычную литературную правку: убирает однообразный
ритм предложений, штампованные связки, канцелярит и общие места,
добавляет конкретики — при этом сюжет, герои и голос автора сохраняются
без изменений. Итоговый текст стоит всегда перечитывать самому перед
публикацией.

## Стек

- Backend: Flask (Python, один файл `app.py`), вызывает Claude API напрямую по HTTP
- Frontend: одна статическая HTML-страница (без сборки)
- Деплой: Render (Web Service), один процесс на весь сервис

## Локальный запуск

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# впишите свой ключ в .env:
# ANTHROPIC_API_KEY=sk-ant-...

export $(grep -v '^#' .env | xargs)   # или используйте python-dotenv
python app.py
```

Откройте http://localhost:8000 — там форма для вставки текста главы или
загрузки файла .txt/.md/.docx.

## Деплой на Render

1. Загрузите этот проект в свой репозиторий на GitHub (см. ниже).
2. На https://dashboard.render.com нажмите **New → Blueprint** и укажите
   ваш репозиторий — Render найдёт `render.yaml` и настроит сервис
   автоматически (план `free`).
   - Либо вручную: **New → Web Service**, подключите репозиторий,
     Build Command — `pip install -r requirements.txt`, Start Command —
     `gunicorn app:app --bind 0.0.0.0:$PORT --timeout 180`.
3. В настройках сервиса (**Environment**) добавьте переменную
   `ANTHROPIC_API_KEY` со своим ключом с https://console.anthropic.com.
   Переменная `ANTHROPIC_MODEL` уже задана в `render.yaml`, но её можно
   поменять на любую доступную вам модель Claude.
4. Дождитесь деплоя — Render выдаст публичный URL вида
   `https://book-chapter-editor.onrender.com`.

Бесплатный план Render «засыпает» сервис после периода бездействия —
первый запрос после паузы может занять 30–60 секунд.

## Публикация в GitHub

```bash
cd book-humanizer
git init
git add .
git commit -m "Initial commit: book chapter editor service"
git branch -M main
git remote add origin https://github.com/<ваш-логин>/<имя-репозитория>.git
git push -u origin main
```

Создайте пустой репозиторий на github.com заранее (без README/лицензии,
чтобы не было конфликтов при первом push), затем подставьте его URL
в команду выше.

## Ограничения

- Обрабатывает главу целиком за один запрос (до ~60 000 символов);
  более длинные главы стоит делить на части.
- Требует собственный ключ Anthropic API (платный по использованию,
  см. https://console.anthropic.com).
- Это инструмент литературной правки, а не сервис для обмана
  детекторов ИИ-текста или платформ — итоговый текст всегда стоит
  вычитывать самому.
