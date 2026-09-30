import asyncio
from maxapi import Bot
from config import MAX_BOT_TOKEN


async def unsubscribe():
    bot = Bot(token=MAX_BOT_TOKEN)
    await bot.delete_webhook()
    print("✅ Подписка на webhook снята")


if __name__ == "__main__":
    asyncio.run(unsubscribe())





# async def subscribe():
#     bot = Bot(token=MAX_BOT_TOKEN)
#     await bot.subscribe_webhook(
#         url="https://ip/webhook",
#         secret="zhkh_bot_2026_secret",
#         update_types=[
#             "message_created",
#             "message_callback",
#             "bot_started",
#         ],
#     )
#     print("✅ Подписка оформлена")
#
#
# if __name__ == "__main__":
#     asyncio.run(subscribe())