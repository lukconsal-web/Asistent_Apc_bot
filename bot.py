import asyncio
import csv
import io
import logging
import os
import re
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
import google.generativeai as genai
import requests

# Логирование
logging.basicConfig(level=logging.INFO)

# ==========================================
# 1. ТОКЕНЫ И НАСТРОЙКИ API
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not BOT_TOKEN or not GEMINI_API_KEY:
  raise ValueError(
      "Не найдены переменные окружения BOT_TOKEN или GEMINI_API_KEY!"
  )

# Инициализация клиента Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")

# ==========================================
# 2. ЗАГРУЗКА PDF ФАЙЛОВ В GEMINI API (БАЗА ЗНАНИЙ)
# ==========================================
PDF_FILES = [
    "ghid_prezentare_legea_187_2022.pdf",
    "condominiu_baza_de_cunostinte.pdf",
]


def load_pdf_sources():
  """Загружает локальные PDF файлы в Gemini Files API при старте"""
  uploaded_files = []
  for filename in PDF_FILES:
    if os.path.exists(filename):
      try:
        logging.info(f"Загрузка файла {filename} в Gemini Files API...")
        uploaded = genai.upload_file(filename)
        uploaded_files.append(uploaded)
        logging.info(f"Файл {filename} успешно подключен: {uploaded.name}")
      except Exception as e:
        logging.error(f"Ошибка загрузки PDF {filename}: {e}")
    else:
      logging.warning(
          f"Файл {filename} не найден в директории бота! Бот продолжит работу"
          " без него."
      )
  return uploaded_files


# Загружаем PDF в память API один раз при старте
PDF_SOURCES = load_pdf_sources()

# ==========================================
# 3. RAG: ЗАГРУЗКА ДАННЫХ ИЗ GOOGLE SHEETS
# ==========================================
URL_LAW = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTfGJg9HzDpc8OL-hCOl64FpZqsri3cePqidISr_SyyhBuLTr0xydZTwzQEu1-jU7xeQT_G3PQ3ksMX/pub?gid=2095782869&single=true&output=csv"
URL_PROCEDURES = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTfGJg9HzDpc8OL-hCOl64FpZqsri3cePqidISr_SyyhBuLTr0xydZTwzQEu1-jU7xeQT_G3PQ3ksMX/pub?gid=48566074&single=true&output=csv"
URL_SOURCES = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTfGJg9HzDpc8OL-hCOl64FpZqsri3cePqidISr_SyyhBuLTr0xydZTwzQEu1-jU7xeQT_G3PQ3ksMX/pub?gid=1501911779&single=true&output=csv"
URL_RULES = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTfGJg9HzDpc8OL-hCOl64FpZqsri3cePqidISr_SyyhBuLTr0xydZTwzQEu1-jU7xeQT_G3PQ3ksMX/pub?gid=1040394502&single=true&output=csv"


def load_csv_from_url(url):
  """Скачивает CSV по ссылке и возвращает список словарей"""
  if "ВСТАВЬТЕ_ССЫЛКУ" in url:
    return []
  try:
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    response.encoding = "utf-8"
    csv_data = io.StringIO(response.text)
    reader = csv.DictReader(csv_data)
    return list(reader)
  except Exception as e:
    logging.error(f"Ошибка загрузки {url}: {e}")
    return []


def load_knowledge_base():
  """Загружает все листы Google Sheets в память сервера"""
  logging.info("Загрузка базы знаний из Google Sheets...")
  db = {
      "law": load_csv_from_url(URL_LAW),
      "procedures": load_csv_from_url(URL_PROCEDURES),
      "sources": load_csv_from_url(URL_SOURCES),
      "rules": load_csv_from_url(URL_RULES),
  }
  logging.info("Google Sheets база знаний загружена!")
  return db


KNOWLEDGE_BASE = load_knowledge_base()


# ==========================================
# 4. ЛОГИКА ПОИСКА ПО ТАБЛИЦЕ
# ==========================================
def search_in_db(query: str, db: dict) -> str:
  """Ищет ключевые слова пользователя в таблицах и формирует контекст"""
  if not db.get("law") and not db.get("procedures"):
    return "Табличный контекст пуст."

  clean_query = re.sub(r"[^\w\s]", "", query).lower()
  keywords = [word for word in clean_query.split() if len(word) > 3]

  if not keywords:
    return "Общий запрос."

  context_fragments = []

  def count_matches(row_dict):
    row_text = " ".join(str(val).lower() for val in row_dict.values() if val)
    return sum(1 for kw in keywords if kw in row_text)

  # ПОИСК: ЗАКОН
  law_matches = []
  for row in db.get("law", []):
    score = count_matches(row)
    if score > 0:
      law_matches.append((score, row))

  law_matches.sort(key=lambda x: x[0], reverse=True)
  for _, row in law_matches[:3]:
    context_fragments.append(
        f"[ЗАКОН ИЗ ТАБЛИЦЫ] Статья: {row.get('Article', '')}. Суть:"
        f" {row.get('Simple explanation', '')}. Пример:"
        f" {row.get('Life example', '')}"
    )

  # ПОИСК: ПРОЦЕДУРЫ
  proc_matches = []
  for row in db.get("procedures", []):
    score = count_matches(row)
    if score > 0:
      proc_matches.append((score, row))

  proc_matches.sort(key=lambda x: x[0], reverse=True)
  for _, row in proc_matches[:2]:
    context_fragments.append(
        f"[ПРОЦЕДУРА ИЗ ТАБЛИЦЫ] Ситуация: {row.get('Situation', '')}. Что"
        f" делать: {row.get('What to do', '')}. Документы:"
        f" {row.get('Documents', '')}. Учреждение:"
        f" {row.get('Institution', '')}"
    )

  if not context_fragments:
    return "В таблице прямых совпадений не найдено."

  return "\n\n".join(context_fragments)


# ==========================================
# 5. ФОРМИРОВАНИЕ ОТВЕТА GEMINI
# ==========================================
async def generate_response_with_rag(user_message: str) -> str:
  table_context = search_in_db(user_message, KNOWLEDGE_BASE)

  system_prompt = f"""
Ты — Asistent APC, профессиональный и доброжелательный юридический AI-помощник по управлению кондоминиумами (APC) в Республике Молдова (Закон №187/2022, актуальная редакция с изменениями).

ИСТОЧНИКИ ЗНАНИЙ И ИХ ПРИОРИТЕТ:
1. Прикрепленный файл "ghid_prezentare_legea_187_2022.pdf" — твой ГЛАВНЫЙ оперативный навигатор. Используй его структуру, схемы действий и матрицу голосования/кворумов.
2. Прикрепленный файл "condominiu_baza_de_cunostinte.pdf" — полная база знаний по каждой статье и параграфу Закона №187/2022.
3. [КОНТЕКСТ ИЗ GOOGLE ТАБЛИЦЫ]:
{table_context}

ПРАВИЛА ОТВЕТА:
- Отвечай ВСЕГДА на том же языке, на котором спросил пользователь (Română sau Rusă).
- Объясняй юридические нормы простыми словами, чтобы понял любой жилец или начинающий администратор.
- СТРОГО ЗАПРЕЩЕНО выдумывать статьи или искажать кворумы голосования. Все цифры и статьи сверяй с прикрепленными документами.

ОБЯЗАТЕЛЬНАЯ СТРУКТУРА ОТВЕТА:
💡 **Суть простыми словами (Esența pe înțelesul tuturor):**
[Краткий, четкий и понятный ответ на вопрос]

📋 **Пошаговые действия / Кто решает (Pași de urgență / Cine decide):**
[Кто имеет полномочия и пошаговый алгоритм: Шаг 1, Шаг 2...]

⚠️ **Что ЗАПРЕЩЕНО законом (Ce NU permite legea):**
[Четкое юридическое предостережение из базы знаний: чего делать нельзя или частая ошибка]

🏢 **Пример из жизни (Exemplu practic):**
[Реальная ситуация из жизни дома для наглядности]

🏛 **Юридическая база (Baza legală):**
[Точная статья Закона 187/2022 или процедура ASP]

Вопрос пользователя: {user_message}
"""

  # Собираем все источники: PDF-файлы + текстовый промпт
  request_contents = []
  if PDF_SOURCES:
    request_contents.extend(PDF_SOURCES)
  request_contents.append(system_prompt)

  try:
    response = await model.generate_content_async(request_contents)
    return response.text
  except Exception as e:
    logging.error(f"Ошибка Gemini API: {e}")
    return (
        "Извините, произошла техническая ошибка при обращении к базе знаний."
        f" ({e})"
    )


# ==========================================
# 6. ИНИЦИАЛИЗАЦИЯ AIOGRAM И ХЭНДЛЕРЫ
# ==========================================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


@dp.message(CommandStart())
async def send_welcome(message: types.Message):
  welcome_text = (
      "Salut! Я Asistent APC — ваш цифровой помощник по вопросам управления "
      "жильем и кондоминиумами в Молдове.\n\n"
      "Я знаю всё по Закону № 187/2022 «О кондоминиуме» (актуальная редакция), "
      "помогу с расчетом кворумов, подготовкой документов и процедурами"
      " регистрации в ASP/Кадастре.\n\n"
      "Задайте мне свой вопрос на русском или румынском языке!"
  )
  await message.answer(welcome_text)


@dp.message(F.text)
async def handle_text(message: types.Message):
  processing_msg = await message.reply("⏳ Анализирую базу знаний Закона 187...")
  reply_text = await generate_response_with_rag(message.text)
  await processing_msg.edit_text(reply_text)


# ==========================================
# ЗАПУСК БОТА
# ==========================================
async def main():
  logging.info("Запуск бота Asistent APC...")
  await bot.delete_webhook(drop_pending_updates=True)
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
