import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from google import genai
from google.genai import types as genai_types

# Логирование
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not BOT_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Не найдены переменные окружения BOT_TOKEN или GEMINI_API_KEY!")

# Инициализация клиента Gemini
ai_client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = """
Ты — экспертный юридический помощник по управлению жильем и кондоминиумами в Республике Молдова.
Твоя база — Закон РМ о кондоминиуме № 187/2022 (со всеми актуальными изменениями), а также правила взаимодействия с ASP (Агентством госуслуг), Cadastru (IP Cadastrul Bunurilor Imobile), Primăria и Гражданским кодексом РМ.

Правила формирования ответов:
1. Отвечай строго на том языке, на котором спросил пользователь (русский или румынский).
2. Форматируй каждый ответ СТРОГО по блокам:
   💡 Суть простыми словами: (коротко, 1-3 предложения).
   🏢 Жизненный пример: (конкретная ситуация из жизни многоквартирного дома).
   🏛 Куда обращаться (если требуется действие): (пошаговый алгоритм: Собрание жильцов / ASP / Cadastru / Primăria / Поставщики услуг / Суд).
   ⚖️ Основание в законе: точные ссылки на статьи и части Закона № 187/2022.

3. Ключевые нормы Молдовы:
   - Единственная форма управления: Asociație de Proprietari din Condominiu (APC). Все старые формы (ACC, APLP, CCL) подлежат трансформации в APC.
   - Земля и общее имущество: придомовая территория формируется и регистрируется в неделимую долевую собственность через решение общего собрания, проект формирования с Примэрией и регистрацию в Кадастре.
   - Взносы и ремонт: резервный фонд обязателен (ст. 53–56), неплательщиков можно привлекать через нотариальный / исполнительный механизм (ст. 59).
   - Если вопрос не регулируется законом № 187/2022, прямо укажи, каким законом или регламентом он регулируется.
"""

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    welcome_text = (
        "👋 Добро пожаловать!\n\n"
        "Я AI-помощник по Закону о кондоминиуме в Молдове (№ 187/2022).\n\n"
        "💡 Вы можете спросить меня о чем угодно, например:\n"
        "• Кто должен оплачивать ремонт крыши или замену стояков?\n"
        "• Как зарегистрировать ассоциацию (APC) в ASP?\n"
        "• Как оформить придомовую землю через Примэрию и Кадастр?\n"
        "• Как проголосовать, если собственник находится за границей?\n\n"
        "Задайте свой вопрос обычными словами!"
    )
    await message.answer(welcome_text)

@dp.message()
async def handle_message(message: types.Message):
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        response = await ai_client.aio.models.generate_content(
            mmodel="gemini-3.6-flash",
            contents=message.text,
            config=genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.2,
            )
        )
        if response.text:
            await message.answer(response.text)
        else:
            await message.answer("Ответ пуст. Попробуйте переформулировать вопрос.")
    except Exception as e:
        logging.error(f"Error handling query: {e}", exc_info=True)
        await message.answer(f"⚠️ Ошибка при обращении к API: {e}")

async def main():
    logging.info("Starting Condo Bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
