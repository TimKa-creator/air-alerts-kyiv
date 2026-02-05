import asyncio
import logging
import os
import aiohttp
from aiogram import Bot
from aiohttp import web

# --- КОНФІГУРАЦІЯ (Беремо з змінних середовища) ---
# Якщо змінних немає, скрипт впаде з помилкою (це добре для безпеки)
API_TOKEN = os.getenv('ALERTS_API_TOKEN')
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHANNEL_ID = '@KylvSkyWatch' 
TARGET_REGION_ID = 31
CHECK_INTERVAL = 15

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- ФЕЙКОВИЙ ВЕБ-СЕРВЕР (Щоб Render дав порт) ---
async def health_check(request):
    return web.Response(text="Bot is running OK")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    # Render.com вимагає слухати порт 10000 або змінну PORT
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Fake web server started on port {port}")

# --- ОСНОВНА ЛОГІКА БОТА ---
class AlertMonitor:
    def __init__(self):
        self.bot = Bot(token=BOT_TOKEN)
        self.last_alert_status = False
        self.headers = {'Authorization': f'Bearer {API_TOKEN}'}

    async def get_alert_status(self):
        url = "https://api.alerts.in.ua/v1/alerts/active.json"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        logger.error(f"API Error: {resp.status}")
                        return None
        except Exception as e:
            logger.error(f"Connection Error: {e}")
            return None

    def find_region_data(self, data, region_id):
        for alert in data.get('alerts', []):
            if int(alert.get('location_uid')) == region_id:
                return alert
        return None

    async def send_telegram_message(self, text):
        try:
            await self.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Telegram Send Error: {e}")

    async def monitor_loop(self):
        logger.info("Bot monitoring loop started...")
        while True:
            data = await self.get_alert_status()
            if data:
                region_alert = self.find_region_data(data, TARGET_REGION_ID)
                is_alert_active = region_alert is not None

                if is_alert_active and not self.last_alert_status:
                    # --- ТРИВОГА ---
                    self.last_alert_status = True
                    await self.send_telegram_message("🔴 <b>ПОВІТРЯНА ТРИВОГА В КИЄВІ!</b>\n\nПройдіть в укриття!")
                    
                    # Причина
                    alert_type = region_alert.get('alert_type')
                    notes = region_alert.get('notes')
                    reason_text = "⚠️ <b>Причина/Тип загрози:</b>\n"
                    
                    if notes:
                        reason_text += f"{notes}"
                    elif alert_type:
                        types_map = {
                            'air_alarm': 'Загальна загроза / Авіація / Ракети',
                            'artillery_shelling': 'Артобстріл',
                            'urban_fights': 'Вуличні бої',
                            'chemical': 'Хімічна загроза',
                            'nuclear': 'Радіаційна загроза'
                        }
                        reason_text += types_map.get(alert_type, "Невідома загроза")
                    else:
                        reason_text += "Інформація уточнюється."

                    await asyncio.sleep(1)
                    await self.send_telegram_message(reason_text)

                elif not is_alert_active and self.last_alert_status:
                    # --- ВІДБІЙ ---
                    self.last_alert_status = False
                    await self.send_telegram_message("🟢 <b>ВІДБІЙ ПОВІТРЯНОЇ ТРИВОГИ В КИЄВІ!</b>")

            await asyncio.sleep(CHECK_INTERVAL)

async def main():
    monitor = AlertMonitor()
    # Запускаємо і веб-сервер, і бота одночасно
    await asyncio.gather(
        start_web_server(),
        monitor.monitor_loop()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped.")