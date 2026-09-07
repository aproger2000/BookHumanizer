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
        self.model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
        self.temperature = float(os.environ.get("GEMINI_TEMPERATURE", "0.85"))
        logger.info(f"GeminiProcessor инициализирован. Модель: {self.model}, температура: {self.temperature}")

    def process(self, text: str, style: str = "neutral") -> str:
        if not self.enabled or len(text) < 100:
            return text

        prompt = self._build_prompt(text, style)
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config={
                    "temperature": self.temperature,
                    "max_output_tokens": len(text) * 2,
                }
            )
            result = response.text
            logger.info(f"Gemini обработал {len(text)} символов -> {len(result)} символов")
            return result
        except Exception as e:
            logger.error(f"Ошибка Gemini: {e}")
            return text

    def _build_prompt(self, text: str, style: str) -> str:
        # Базовые инструкции (общие для всех стилей)
        base_instructions = (
            "Ты — редактор художественных текстов. Твоя задача — переписать следующий текст так, "
            "чтобы он звучал максимально естественно, живо и по-человечески. "
            "Следуй этим правилам:\n"
            "1. Сохрани ВСЕХ персонажей, их имена, диалоги и ключевые события.\n"
            "2. ИЗБЕГАЙ канцеляризмов (например: 'в связи с тем, что' → 'потому что', 'осуществлять' → 'делать').\n"
            "3. УБЕРИ маркеры ИИ: 'стоит отметить', 'следует подчеркнуть', 'важно понимать', 'несомненно' и т.п.\n"
            "4. Добавь лёгкую разговорность: частицы ('же', 'ведь', 'бы'), вводные слова ('впрочем', 'кстати'), "
            "междометия ('ну', 'ой', 'ах'), но НЕ переусердствуй — они должны звучать естественно.\n"
            "5. Сделай предложения разной длины: чередуй короткие и длинные.\n"
            "6. Используй живые глаголы и конкретные детали.\n"
            "7. НЕ меняй смысл и НЕ добавляй новую информацию.\n"
            "8. Верни ТОЛЬКО переписанный текст, без лишних комментариев.\n\n"
        )

        # Специфичные для стиля дополнения
        style_prompts = {
            "neutral": (
                "Сделай текст естественным и лёгким для чтения. Он должен звучать как рассказ "
                "хорошего автора, но без излишней вычурности. Сохрани ритм и атмосферу.\n\n"
            ),
            "dynamic_scifi": (
                "Это научная фантастика. Сделай текст более динамичным, напряжённым и образным. "
                "Используй короткие предложения для экшена, добавляй эмоциональные восклицания и "
                "метафоры. Сохрани технолексику, но сделай её более естественной.\n\n"
            ),
            "literary": (
                "Это литературная проза. Сделай текст более глубоким, образным и атмосферным. "
                "Добавь больше описаний, внутренних монологов, метафор. Сохрани плавность и "
                "красоту языка, но избегай излишней высокопарности.\n\n"
            ),
        }

        # Полный промпт = базовые инструкции + стилевые дополнения + текст
        full_prompt = base_instructions + style_prompts.get(style, style_prompts["neutral"]) + text
        return full_prompt
