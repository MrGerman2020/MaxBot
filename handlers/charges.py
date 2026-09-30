import aiohttp
import logging
from maxapi import Router, F
from maxapi.types import MessageCreated
from maxapi.context import MemoryContext
from services.ocr import extract_text_from_image
from states import Form
from keyboards import home_kb

router = Router()
logger = logging.getLogger(__name__)


async def download_file(url: str) -> bytes:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.read()


@router.message_created(Form.waiting_receipt, F.message.body.attachments)
async def handle_receipt(event: MessageCreated, context: MemoryContext):
    """Обработка фото квитанции."""
    attachments = event.message.body.attachments
    if not attachments:
        return

    for att in attachments:
        logger.info(f"RECEIPT ATTACH: {att}")

        # Тип вложения — 'image'
        att_type = getattr(att, "type", None)
        if att_type != "image":
            continue

        # Данные в payload
        payload = getattr(att, "payload", None)
        if not payload:
            continue

        # URL уже готов в payload.url
        url = getattr(payload, "url", None)
        if not url:
            await event.message.answer(
                "⚠️ Не удалось получить ссылку на фото.",
                attachments=[home_kb()],
            )
            return

        try:
            file_bytes = await download_file(url)
            text = await extract_text_from_image(file_bytes)
            await context.clear()

            if text.strip():
                preview = text[:1000] + ("..." if len(text) > 1000 else "")
                await event.message.answer(
                    f"📄 *Распознанный текст квитанции:*\n\n{preview}",
                    attachments=[home_kb()],
                )
            else:
                await event.message.answer(
                    "⚠️ Не удалось распознать текст. Фото чёткое?",
                    attachments=[home_kb()],
                )
        except Exception as e:
            logger.exception(f"Ошибка обработки квитанции: {e}")
            await event.message.answer(f"❌ Ошибка: {e}", attachments=[home_kb()])
        return