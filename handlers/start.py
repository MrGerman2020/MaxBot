from maxapi import Router, F
from maxapi.types import MessageCreated, BotStarted
from keyboards import main_menu
from texts import WELCOME_TEXT

router = Router()

WELCOME = WELCOME_TEXT


@router.bot_started()
async def on_bot_started(event: BotStarted):
    await event.bot.send_message(
        chat_id=event.chat_id,
        text=WELCOME,
        attachments=[main_menu()],
    )


@router.message_created(F.message.body.text == "/start")
async def cmd_start(event: MessageCreated):
    await event.message.answer(WELCOME, attachments=[main_menu()])
