import asyncio
import logging
import os
import aiohttp
from aiogram import Bot
from aiohttp import web
from telethon import TelegramClient
from telethon.sessions import StringSession
import google.generativeai as genai
from datetime import datetime, timezone

# --- КОНФІГУРАЦІЯ ---
API_TOKEN = os.getenv('ALERTS_API_TOKEN')
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
CHANNEL_ID = '@KyivAegis' 
TARGET_REGION_ID = 31 # Київ
CHECK_INTERVAL = 15

# Telethon
TELEGRAM_API_ID = int(os.getenv('TG_API_ID'))
TELEGRAM_API_HASH = os.getenv('TG_API_HASH')
TELEGRAM_SESSION = os.getenv('TG_SESSION')
SOURCE_CHANNEL = 'kpszsu' 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- AI REPHRASER ---
class AIRephraser:
    def __init__(self, api_key):
        if api_key:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel('gemini-1.5-flash')
            self.is_active = True
        else:
            self.is_active = False

    async def rephrase(self, text, is_update=False):
        if not self.is_active or not text:
            return text
        
        # Різні промпти для початку тривоги і для оновлень
        context = "початок тривоги" if not is_update else "оновлення ситуації під час тривоги"
        
        prompt = (
                f"Ти - оперативний черговий системи 'Kyiv Aegis'. "
                f"Твоє завдання: переписати вхідне повідомлення про повітряну загрозу. "
                f"Вимоги:\n"
                f"1. Пиши лаконічно, спокійно, по-військовому чітко. Українською мовою.\n"
                f"2. Використовуй HTML теги для форматування: <b>жирний</b> для важливого, "
                f"<i>курсив</i> для деталей. НЕ використовуй Markdown (** або __).\n"
                f"3. Прибери зайві емодзі, залиш тільки суть (тип ракети, напрямок, час підльоту).\n"
                f"4. Не згадуй джерела і не пиши вступних слів типу 'Ось переписане повідомлення'.\n"
                f"Вхідний текст: {text}"
        )
        try:
            response = await asyncio.to_thread(self.model.generate_content, prompt)
            return response.text.strip()
        except Exception as e:
            logger.error(f"AI Error: {e}")
            return text

# --- WEB SERVER ---
async def health_check(request):
    return web.Response(text="Kyiv Aegis: LIVE MONITORING ACTIVE")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

# --- ОСНОВНА ЛОГІКА ---
class AlertMonitor:
    def __init__(self):
        self.bot = Bot(token=BOT_TOKEN)
        self.headers = {'Authorization': f'Bearer {API_TOKEN}'}
        self.client = TelegramClient(StringSession(TELEGRAM_SESSION), TELEGRAM_API_ID, TELEGRAM_API_HASH)
        self.ai = AIRephraser(GEMINI_API_KEY)
        
        # СТАН
        self.last_alert_status = False
        self.last_processed_msg_id = None # Запам'ятовуємо останнє повідомлення

    async def get_alert_status(self):
        url = "https://api.alerts.in.ua/v1/alerts/active.json"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers) as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception:
            pass
        return None

    async def get_latest_message_from_channel(self):
        """Повертає об'єкт повідомлення або None"""
        try:
            messages = await self.client.get_messages(SOURCE_CHANNEL, limit=1)
            if not messages: return None
            
            msg = messages[0]
            # Перевірка свіжості (10 хв)
            if (datetime.now(timezone.utc) - msg.date).total_seconds() > 600:
                return None
            return msg
        except Exception as e:
            logger.error(f"Telethon Error: {e}")
            return None

    def find_region_data(self, data, region_id):
        for alert in data.get('alerts', []):
            if int(alert.get('location_uid')) == region_id:
                return alert
        return None

    async def send_message(self, text):
        try:
            await self.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Send Error: {e}")

    async def monitor_loop(self):
        await self.client.connect()
        logger.info("Monitor started.")

        while True:
            data = await self.get_alert_status()
            if data:
                region_alert = self.find_region_data(data, TARGET_REGION_ID)
                is_alert_active = region_alert is not None

                # 1. ПОЧАТОК ТРИВОГИ
                if is_alert_active and not self.last_alert_status:
                    self.last_alert_status = True
                    await self.send_message("🔴 <b>ПОВІТРЯНА ТРИВОГА В КИЄВІ!</b>\n\nНегайно в укриття!")
                    
                    # Отримуємо першу причину
                    msg = await self.get_latest_message_from_channel()
                    if msg:
                        self.last_processed_msg_id = msg.id
                        ai_text = await self.ai.rephrase(msg.text, is_update=False)
                        await asyncio.sleep(2)
                        await self.send_message(f"⚠️ <b>Загроза:</b>\n{ai_text}")

                # 2. ТРИВОГА ТРИВАЄ (МОНІТОРИНГ ОНОВЛЕНЬ)
                elif is_alert_active and self.last_alert_status:
                    # Перевіряємо, чи є нові повідомлення
                    msg = await self.get_latest_message_from_channel()
                    
                    # Якщо повідомлення є, воно нове (ID змінився) і текст не порожній
                    if msg and msg.id != self.last_processed_msg_id:
                        self.last_processed_msg_id = msg.id # Запам'ятовуємо нове ID
                        
                        logger.info("New update detected!")
                        ai_text = await self.ai.rephrase(msg.text, is_update=True)
                        
                        # Відправляємо оновлення
                        await self.send_message(f"📡 <b>Оновлення ситуації:</b>\n{ai_text}")

                # 3. ВІДБІЙ
                elif not is_alert_active and self.last_alert_status:
                    self.last_alert_status = False
                    self.last_processed_msg_id = None # Скидаємо пам'ять
                    await self.send_message("🟢 <b>ВІДБІЙ ПОВІТРЯНОЇ ТРИВОГИ!</b>")

            await asyncio.sleep(CHECK_INTERVAL)

async def main():
    monitor = AlertMonitor()
    await asyncio.gather(start_web_server(), monitor.monitor_loop())

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass