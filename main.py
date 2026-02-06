import asyncio
import logging
import os
import aiohttp
from aiogram import Bot
from aiohttp import web
from telethon import TelegramClient
from telethon.sessions import StringSession
from datetime import datetime, timedelta, timezone

# --- КОНФІГУРАЦІЯ ---
API_TOKEN = os.getenv('ALERTS_API_TOKEN')
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHANNEL_ID = '@KylvSkyWatch' 
TARGET_REGION_ID = 31 # Київ
CHECK_INTERVAL = 15

# Налаштування Userbot (Telethon)
TELEGRAM_API_ID = int(os.getenv('TG_API_ID'))
TELEGRAM_API_HASH = os.getenv('TG_API_HASH')
TELEGRAM_SESSION = os.getenv('TG_SESSION') # Той довгий рядок

# Канал, звідки крадемо інфу (наприклад, @kiev_air_raid або офіційний @kpszsu)
# Встав сюди юзернейм каналу-донора (без @)
SOURCE_CHANNEL = 'air_alert_ua' 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- WEB SERVER (Render keep-alive) ---
async def health_check(request):
    return web.Response(text="Bot & Userbot running")

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
        self.last_alert_status = False
        self.headers = {'Authorization': f'Bearer {API_TOKEN}'}
        # Ініціалізація клієнта для читання (Userbot)
        self.client = TelegramClient(StringSession(TELEGRAM_SESSION), TELEGRAM_API_ID, TELEGRAM_API_HASH)

    async def get_alert_status(self):
        url = "https://api.alerts.in.ua/v1/alerts/active.json"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers) as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception as e:
            logger.error(f"API Error: {e}")
        return None

    async def get_reason_from_channel(self):
        """Читає останнє повідомлення з каналу-донора"""
        try:
            # Отримуємо останнє повідомлення
            messages = await self.client.get_messages(SOURCE_CHANNEL, limit=1)
            if not messages:
                return None
            
            msg = messages[0]
            
            # Перевірка: чи повідомлення свіже (не старіше 10 хв)
            # Важливо: час має бути в UTC для порівняння
            msg_date = msg.date
            now = datetime.now(timezone.utc)
            
            if (now - msg_date).total_seconds() > 600:
                logger.info("Повідомлення в каналі-донорі старе")
                return None

            return msg.text
        except Exception as e:
            logger.error(f"Error reading channel: {e}")
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
        # Запускаємо клієнт Telethon
        await self.client.connect()
        logger.info("Userbot connected!")

        while True:
            data = await self.get_alert_status()
            if data:
                region_alert = self.find_region_data(data, TARGET_REGION_ID)
                is_alert_active = region_alert is not None

                if is_alert_active and not self.last_alert_status:
                    # --- ТРИВОГА ПОЧАЛАСЯ ---
                    self.last_alert_status = True
                    await self.send_message("🔴 <b>ПОВІТРЯНА ТРИВОГА В КИЄВІ!</b>\n\nПройдіть в укриття!")
                    
                    # Спроба 1: Взяти з API
                    api_notes = region_alert.get('notes')
                    
                    # Спроба 2: Взяти з каналу (якщо API пусте або неточне)
                    channel_reason = await self.get_reason_from_channel()
                    
                    reason_text = "⚠️ <b>Причина:</b>\n"
                    
                    if channel_reason:
                         # Тут можна додати логіку фільтрації тексту
                        reason_text += f"<i>(За даними моніторингових каналів):</i>\n{channel_reason}"
                    elif api_notes:
                        reason_text += api_notes
                    else:
                        reason_text += "Загроза встановлюється. Слідкуйте за оновленнями."

                    await asyncio.sleep(2)
                    await self.send_message(reason_text)

                elif not is_alert_active and self.last_alert_status:
                    # --- ВІДБІЙ ---
                    self.last_alert_status = False
                    await self.send_message("🟢 <b>ВІДБІЙ ПОВІТРЯНОЇ ТРИВОГИ В КИЄВІ!</b>")

            await asyncio.sleep(CHECK_INTERVAL)

async def main():
    monitor = AlertMonitor()
    await asyncio.gather(
        start_web_server(),
        monitor.monitor_loop()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass