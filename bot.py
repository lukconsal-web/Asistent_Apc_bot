import os
import asyncio
import logging
import csv
import io
import re
import requests
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
import google.generativeai as genai

# Логирование
logging.basicConfig(level=logging.INFO)

# ==========================================
# 1. ТОКЕНЫ И НАСТРОЙКИ API
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not BOT_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Не найдены переменные окружения BOT_TOKEN или GEMINI_API_KEY!")

# Инициализация клиента Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

# ==========================================
# 2. RAG: ЗАГРУЗКА БАЗЫ ЗНАНИЙ ИЗ GOOGLE SHEETS
# ==========================================
# ВСТАВЬТЕ СЮДА ВАШИ 4 ССЫЛКИ В ФОРМАТЕ CSV:
URL_LAW = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTfGJg9HzDpc8OL-hCOl64FpZqsri3cePqidISr_SyyhBuLTr0xydZTwzQEu1-jU7xeQT_G3PQ3ksMX/pub?gid=2095782869&single=true&output=csv"
URL_PROCEDURES = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTfGJg9HzDpc8OL-hCOl64FpZqsri3cePqidISr_SyyhBuLTr0xydZTwzQEu1-jU7xeQT_G3PQ3ksMX/pub?gid=48566074&single=true&output=csv"
URL_SOURCES = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTfGJg9HzDpc8OL-hCOl64FpZqsri3cePqidISr_SyyhBuLTr0xydZTwzQEu1-jU7xeQT_G3PQ3ksMX/pub?gid=1501911779&single=true&output=csv"
URL_RULES = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTfGJg9HzDpc8OL-hCOl64FpZqsri3cePqidISr_SyyhBuLTr0xydZTwzQEu1-jU7xeQT_G3PQ3ksMX/pub?gid=1040394502&single=true&output=csv"

def load_csv_from_url(url):
    """Скачивает CSV по ссылке и возвращает список словарей"""
    if "ВСТАВЬТЕ_ССЫЛКУ" in url:
        return [] # Защита от пустых ссылок
    
    try:
        response = requests.get(url)
        response.raise_for_status()
        response.encoding = 'utf-8'
        csv_data = io.StringIO(response.text)
        reader = csv.DictReader(csv_data)
        return list(reader)
    except Exception as e:
        logging.error(f"Ошибка загрузки {url}: {e}")
        return []

def load_knowledge_base():
    """Загружает все 4 листа в память сервера"""
    logging.info("Загрузка базы знаний из Google Sheets...")
    db = {
        'law': load_csv_from_url(URL_LAW),
        'procedures': load_csv_from_url(URL_PROCEDURES),
        'sources': load_csv_from_url(URL_SOURCES),
        'rules': load_csv_from_url(URL_RULES)
    }
    logging.info("База знаний загружена!")
    return db

# Загружаем базу при старте бота один раз
KNOWLEDGE_BASE = load_knowledge_base()

# ==========================================
# 3. ЛОГИКА ПОИСКА ПО БАЗЕ
# ==========================================
def search_in_db(query: str, db: dict) -> str:
    """Ищет ключевые слова пользователя в базе и формирует контекст"""
    if not db.get('law') and not db.get('procedures'):
        return "Ошибка: База знаний пуста. Проверьте ссылки на CSV."

    # Очищаем запрос от знаков препинания и разбиваем на слова (длиной > 3 букв)
    clean_query = re.sub(r'[^\w\s]', '', query).lower()
    keywords = [word for word in clean_query.split() if len(word) > 3]
    
    if not keywords:
        return "Пожалуйста, задайте более конкретный вопрос."

    context_fragments = []

    def count_matches(row_dict):
        row_text = " ".join(str(val).lower() for val in row_dict.values() if val)
        return sum(1 for kw in keywords if kw in row_text)

    # ПОИСК: ЗАКОН
    law_matches = []
    for row in db['law']:
        score = count_matches(row)
        if score > 0:
            law_matches.append((score, row))
    
    law_matches.sort(key=lambda x: x[0], reverse=True)
    for _, row in law_matches[:3]: # Берем топ-3 совпадения
        context_fragments.append(
            f"[ЗАКОН] Статья: {row.get('Article', '')}. "
            f"Суть: {row.get('Simple explanation', '')}. "
            f"Пример: {row.get('Life example', '')}"
        )

    # ПОИСК: ПРОЦЕДУРЫ
    proc_matches = []
    for row in db['procedures']:
        score = count_matches(row)
        if score > 0:
            proc_matches.append((score, row))
    
    proc_matches.sort(key=lambda x: x[0], reverse=True)
    for _, row in proc_matches[:2]: # Берем топ-2
        context_fragments.append(
            f"[ПРОЦЕДУРА] Ситуация: {row.get('Situation', '')}. "
            f"Что делать: {row.get('What to do', '')}. "
            f"Документы: {row.get('Documents', '')}. "
            f"Учреждение: {row.get('Institution', '')}"
        )

    if not context_fragments:
        return "В базе знаний ничего не найдено."
    
    return "\n\n".join(context_fragments)

# ==========================================
# 4. ФОРМИРОВАНИЕ ОТВЕТА GEMINI
# ==========================================
async def generate_response_with_rag(user_message: str) -> str:
    # Ищем контекст в таблицах
    context = search_in_db(user_message, KNOWLEDGE_BASE)
    
    prompt = f"""
    Ты — Asistent APC, цифровой юридический помощник по кондоминиумам в Молдове (Закон №187/2022).
    Твоя задача — отвечать на вопрос пользователя СТРОГО на основе информации из блока [НАЙДЕННЫЙ КОНТЕКСТ].
    
    ПРАВИЛА ОТВЕТА:
    1. Если в контексте написано "В базе знаний ничего не найдено", честно скажи: "К сожалению, я не нашел точного ответа в моей базе Закона 187/2022. Пожалуйста, уточните или переформулируйте вопрос."
    2. Не выдумывай законы, штрафы или процедуры, которых нет в контексте!
    3. Отвечай вежливо, структурированно, используй списки для читаемости.
    4. Обязательно указывай номер статьи закона, если ссылаешься на нее.

    [НАЙДЕННЫЙ КОНТЕКСТ]:
    {context}

    Вопрос пользователя: {user_message}
    """
    
    try:
        # Используем нативный асинхронный метод Gemini
        response = await model.generate_content_async(prompt)
        return response.text
    except Exception as e:
        logging.error(f"Ошибка Gemini API: {e}")
        # Теперь бот пришлет саму ошибку прямо вам в Телеграм!
        return f"Техническая ошибка Gemini API: {e}"

# ==========================================
# 5. ИНИЦИАЛИЗАЦИЯ AIOGRAM И ХЭНДЛЕРЫ
# ==========================================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(CommandStart())
async def send_welcome(message: types.Message):
    welcome_text = (
        "Salut! Я Asistent APC — ваш цифровой помощник по вопросам управления "
        "жильем и кондоминиумами в Молдове.\n\n"
        "Я могу ответить на вопросы по Закону № 187/2022 «О кондоминиуме», "
        "а также подсказать процедуры регистрации в ASP.\n\n"
        "Задайте мне свой вопрос!"
    )
    await message.answer(welcome_text)

@dp.message(F.text)
async def handle_text(message: types.Message):
    # Отправляем сообщение-заглушку "Печатает..."
    processing_msg = await message.reply("⏳ Ищу точный ответ в базе знаний...")
    
    # Генерируем ответ
    reply_text = await generate_response_with_rag(message.text)
    
    # Заменяем заглушку на готовый ответ
    await processing_msg.edit_text(reply_text)

# ==========================================
# ЗАПУСК БОТА
# ==========================================
async def main():
    logging.info("Запуск бота Asistent APC...")
    # Удаляем вебхуки, чтобы бот точно работал в режиме polling
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
