import asyncio
import csv
import io
import logging
import os
import re
import time
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup
import edge_tts
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
model = genai.GenerativeModel("gemini-3.6-flash")

# ==========================================
# 2. ЗАГРУЗКА PDF ФАЙЛОВ В GEMINI API (БАЗА ЗНАНИЙ)
# ==========================================
PDF_FILES = [
    "ghid_prezentare_legea_187_2022.pdf",
    "condominiu_baza_de_cunostinte.pdf",
]


def load_pdf_sources():
  """Загружает локальные PDF файлы в Gemini Files API и проверяет статус ACTIVE"""
  uploaded_files = []
  for filename in PDF_FILES:
    if os.path.exists(filename):
      try:
        logging.info(f"Загрузка файла {filename} в Gemini Files API...")
        uploaded = genai.upload_file(filename)

        # Ожидание готовности файла (статус ACTIVE)
        retries = 0
        while uploaded.state.name == "PROCESSING" and retries < 15:
          time.sleep(2)
          uploaded = genai.get_file(uploaded.name)
          retries += 1

        if uploaded.state.name == "ACTIVE":
          uploaded_files.append(uploaded)
          logging.info(f"Файл {filename} готов: {uploaded.name}")
        else:
          logging.warning(
              f"Файл {filename} в статусе {uploaded.state.name}, продолжаем без"
              " него"
          )
      except Exception as e:
        logging.error(f"Ошибка загрузки PDF {filename}: {e}")
    else:
      logging.warning(f"Файл {filename} не найден в директории бота!")
  return uploaded_files


PDF_SOURCES = load_pdf_sources()

# ==========================================
# 3. RAG: ЗАГРУЗКА ДАННЫХ ИЗ GOOGLE SHEETS
# ==========================================
URL_LAW = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTfGJg9HzDpc8OL-hCOl64FpZqsri3cePqidISr_SyyhBuLTr0xydZTwzQEu1-jU7xeQT_G3PQ3ksMX/pub?gid=2095782869&single=true&output=csv"
URL_PROCEDURES = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTfGJg9HzDpc8OL-hCOl64FpZqsri3cePqidISr_SyyhBuLTr0xydZTwzQEu1-jU7xeQT_G3PQ3ksMX/pub?gid=48566074&single=true&output=csv"
URL_SOURCES = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTfGJg9HzDpc8OL-hCOl64FpZqsri3cePqidISr_SyyhBuLTr0xydZTwzQEu1-jU7xeQT_G3PQ3ksMX/pub?gid=1501911779&single=true&output=csv"
URL_RULES = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTfGJg9HzDpc8OL-hCOl64FpZqsri3cePqidISr_SyyhBuLTr0xydZTwzQEu1-jU7xeQT_G3PQ3ksMX/pub?gid=1040394502&single=true&output=csv"


def load_csv_from_url(url):
  try:
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    response.encoding = "utf-8"
    csv_data = io.StringIO(response.text)
    reader = csv.DictReader(csv_data)
    return list(reader)
  except Exception as e:
    logging.error(f"Ошибка загрузки CSV {url}: {e}")
    return []


def load_knowledge_base():
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

  # Поиск по статьям
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

  # Поиск по процедурам
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
Ты — Asistent APC, профессиональный юридический AI-помощник по управлению кондоминиумами (APC) в Республике Молдова (Закон №187/2022, актуальная редакция с изменениями).

ИСТОЧНИКИ ЗНАНИЙ:
1. "ghid_prezentare_legea_187_2022.pdf" — твой главный оперативный навигатор.
2. "condominiu_baza_de_cunostinte.pdf" — полная база знаний по статьям и параграфам.
3. [КОНТЕКСТ ИЗ GOOGLE ТАБЛИЦЫ]:
{table_context}

ПРАВИЛА ОТВЕТА:
- Отвечай ВСЕГДА на том же языке, на котором спросил пользователь (Română sau Rusă).
- Объясняй юридические нормы доступным языком.
- СТРОГО ЗАПРЕЩЕНО выдумывать статьи или менять установленные законом кворумы.

ОБЯЗАТЕЛЬНАЯ СТРУКТУРА ОТВЕТА:
💡 **Суть простыми словами (Esența pe înțelesul tuturor):**
[Краткий, четкий и понятный ответ на вопрос]

📋 **Пошаговые действия / Кто решает (Pași de urgență / Cine decide):**
[Кто уполномочен и пошаговый алгоритм: Шаг 1, Шаг 2...]

⚠️ **Что ЗАПРЕЩЕНО законом (Ce NU permite legea):**
[Предостережение из базы знаний: чего делать нельзя или частая ошибка]

🏢 **Пример из жизни (Exemplu practic):**
[Наглядная ситуация из жизни дома]

🏛 **Юридическая база (Baza legală):**
[Точная статья Закона 187/2022 или процедура ASP/Кадастра]

Вопрос пользователя: {user_message}
"""

  request_contents = []
  if PDF_SOURCES:
    request_contents.extend(PDF_SOURCES)
  request_contents.append(system_prompt)

  try:
    response = await model.generate_content_async(request_contents)
    return response.text
  except Exception as e:
    logging.error(f"Ошибка Gemini API с файлами: {e}, резервный запрос...")
    try:
      # Резервный запрос без файлов, если файл устарел или возник сбой
      fallback_resp = await model.generate_content_async(system_prompt)
      return fallback_resp.text
    except Exception as e2:
      logging.error(f"Критическая ошибка Gemini API: {e2}")
      return f"Произошла техническая ошибка: ({e2})"


# ==========================================
# 6. ОЗВУЧКА ТЕКСТА (NEURAL TTS)
# ==========================================
def clean_text_for_voice(text: str) -> str:
  text = re.sub(r"[\*#_`]", "", text)
  text = re.sub(r"[💡📋⚠️🏢🏛📌🔊⏳]", "", text)
  return text.strip()


async def generate_voice_audio(text: str) -> bytes:
  cleaned = clean_text_for_voice(text)
  # Берем первые 2500 символов для комфортной длины аудиосообщения
  if len(cleaned) > 2500:
    cleaned = cleaned[:2500] + "..."

  has_cyrillic = bool(re.search(r"[а-яА-ЯёЁ]", cleaned))
  voice = "ru-RU-DmitryNeural" if has_cyrillic else "ro-RO-EmilNeural"

  communicate = edge_tts.Communicate(cleaned, voice)
  audio_buffer = io.BytesIO()

  async for chunk in communicate.stream():
    if chunk["type"] == "audio":
      audio_buffer.write(chunk["data"])

  audio_buffer.seek(0)
  return audio_buffer.read()


def get_voice_keyboard() -> InlineKeyboardMarkup:
  button = InlineKeyboardButton(
      text="🔊 Озвучить ответ / Ascultă", callback_data="play_voice"
  )
  return InlineKeyboardMarkup(inline_keyboard=[[button]])


def split_message(text: str, max_len: int = 3800) -> list:
  """Разбивает текст на части по переносам строк, не превышая лимит Telegram"""
  chunks = []
  while len(text) > max_len:
    split_idx = text.rfind("\n", 0, max_len)
    if split_idx == -1:
      split_idx = max_len
    chunks.append(text[:split_idx].strip())
    text = text[split_idx:].strip()
  if text:
    chunks.append(text)
  return chunks


# ==========================================
# 7. ХЭНДЛЕРЫ AIOGRAM
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

  try:
    reply_text = await generate_response_with_rag(message.text)
    chunks = split_message(reply_text)

    # Если ответ уместился в одно сообщение:
    if len(chunks) == 1:
      await processing_msg.edit_text(
          chunks[0], reply_markup=get_voice_keyboard()
      )
    else:
      # Если ответ длинный: редактируем первое сообщение, а остальные отправляем следом
      await processing_msg.edit_text(chunks[0])
      for i, chunk in enumerate(chunks[1:]):
        # Кнопку озвучки вешаем на последнее сообщение
        keyboard = (
            get_voice_keyboard() if i == len(chunks[1:]) - 1 else None
        )
        await message.answer(chunk, reply_markup=keyboard)

  except Exception as e:
    logging.error(f"Ошибка при обработке и отправке сообщения: {e}")
    try:
      await processing_msg.edit_text(f"⚠️ Ошибка при отправке ответа: {e}")
    except Exception:
      pass


@dp.callback_query(F.data == "play_voice")
async def handle_voice_callback(callback: types.CallbackQuery):
  await callback.answer("⏳ Создаю аудиозапись...")

  message_text = callback.message.text
  if not message_text:
    await callback.message.answer("Не удалось прочитать текст для озвучки.")
    return

  try:
    audio_bytes = await generate_voice_audio(message_text)
    voice_file = BufferedInputFile(audio_bytes, filename="voice_answer.ogg")

    await callback.message.reply_voice(
        voice=voice_file, caption="🔊 Голосовая версия ответа"
    )
  except Exception as e:
    logging.error(f"Ошибка озвучки: {e}")
    await callback.message.answer(f"Не удалось сгенерировать аудио: {e}")


# ==========================================
# ЗАПУСК БОТА
# ==========================================
async def main():
  logging.info("Запуск бота Asistent APC...")
  await bot.delete_webhook(drop_pending_updates=True)
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
