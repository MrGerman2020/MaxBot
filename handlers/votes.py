import aiohttp
import logging
from maxapi import Router, F
from maxapi.types import MessageCreated, MessageCallback
from maxapi.context import MemoryContext
from keyboards import confirm_vote_kb, home_kb
from database import save_protocol, update_protocol_status
from services.ocr import extract_text_from_image, extract_owners_from_protocol
from states import Form

router = Router()
logger = logging.getLogger(__name__)


async def download_file(url: str) -> bytes:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.read()


@router.message_created(Form.waiting_protocol, F.message.body.attachments)
async def handle_protocol_file(event: MessageCreated, context: MemoryContext):
    """Обработка фото протокола."""
    attachments = event.message.body.attachments
    if not attachments:
        return

    for att in attachments:
        logger.info(f"PROTOCOL ATTACH: {att}")

        att_type = getattr(att, "type", None)
        if att_type != "image":
            continue

        payload = getattr(att, "payload", None)
        if not payload:
            continue

        url = getattr(payload, "url", None)
        if not url:
            await event.message.answer(
                "⚠️ Не удалось получить ссылку на файл.",
                attachments=[home_kb()],
            )
            return

        try:
            file_bytes = await download_file(url)
            ocr_text = await extract_text_from_image(file_bytes)
            owners = extract_owners_from_protocol(ocr_text)

            chat_id, user_id = event.get_ids()

            protocol_id = await save_protocol(
                user_id=user_id,
                address="(уточнить)",
                ocr_text=ocr_text,
                owners_found=", ".join(owners[:20]),
            )
            await context.clear()

            preview = ocr_text[:400] + ("..." if len(ocr_text) > 400 else "")
            owners_str = ", ".join(owners[:10]) if owners else "не найдены"

            await event.message.answer(
                f"📄 Протокол обработан (ID: {protocol_id}).\n\n"
                f"Распознанный текст (фрагмент):\n{preview}\n\n"
                f"Найдено подписантов: {owners_str}\n\n"
                f"Подтвердите или оспорьте:",
                attachments=[confirm_vote_kb(protocol_id)],
            )
        except Exception as e:
            logger.exception(f"Ошибка обработки протокола: {e}")
            await event.message.answer(f"❌ Ошибка: {e}", attachments=[home_kb()])
        return


@router.message_callback()
async def vote_callback(event: MessageCallback):
    payload = event.callback.payload or ""

    if payload.startswith("vote:confirm:"):
        protocol_id = int(payload.split(":")[2])
        await update_protocol_status(protocol_id, "confirmed")
        await event.message.answer(
            "✅ Принято. Ваша подпись подтверждена.",
            attachments=[home_kb()],
        )
        await event.ack(notification="Подтверждено")

    elif payload.startswith("vote:dispute:"):
        protocol_id = int(payload.split(":")[2])
        await update_protocol_status(protocol_id, "disputed")
        await event.message.answer(
            "❌ Зафиксировано оспаривание.\n\n"
            "1. Запросите в УК оригинал протокола.\n"
            "2. Подайте заявление в прокуратуру.\n"
            "3. Иск в суд о признании решения недействительным.",
            attachments=[home_kb()],
        )
        await event.ack(notification="Оспорено")

    else:
        await event.ack(notification=f"Неизвестный payload: {payload}")