import os
import logging
from google import genai

logger = logging.getLogger(__name__)

class GeminiProcessor:
    def __init__(self):
        self.api_key = os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            logger.warning("GEMINI_API_KEY не задан, Gemini-обработка отключена")
            self.enabled = False
            return
        self.client = genai.Client(api_key=self.api_key)
        self.enabled = True
        self.model = "gemini-2.0-flash"  # или gemini-3.5-flash, gemini-3.7-flash[reference:1]

    def process(self, text: str, style: str = "neutral") -> str:
        """
        Отправляет текст в Gemini для перефразирования с сохранением смысла.
        """
        if not self.enabled or len(text) < 100:
            return text

        prompt = self._build_prompt(text, style)
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config={
                    "temperature": 0.85,
                    "max_output_tokens": len(text) * 2,  # запас для генерации
                }
            )
            result = response.text
            logger.info(f"Gemini обработал {len(text)} символов -> {len(result)} символов")
            return result
        except Exception as e:
            logger.error(f"Ошибка Gemini: {e}")
            return text

    def _build_prompt(self, text: str, style: str) -> str:
        prompts = {
            "neutral": (
                "Перепиши следующий текст на русском языке, сохраняя смысл, "
                "но делая его более естественным и живым. Избегай канцеляризмов, "
                "шаблонных фраз и маркеров ИИ. Сохрани все имена персонажей, "
                "диалоги и ключевые детали сюжета. Верни только переписанный текст:\n\n"
            ),
            "dynamic_scifi": (
                "Перепиши следующий научно-фантастический текст, сделав его "
                "более динамичным, образным и напряжённым. Используй яркие метафоры, "
                "короткие предложения для экшена и эмоциональные диалоги. "
                "Сохрани всех персонажей и сюжет. Верни только текст:\n\n"
            ),
        }
        return prompts.get(style, prompts["neutral"]) + text
