import pytesseract
from PIL import Image
import io
import re
import logging

# ✅ Указываем путь к tesseract.exe явно
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

logger = logging.getLogger(__name__)


async def extract_text_from_image(file_bytes: bytes) -> str:
    try:
        image = Image.open(io.BytesIO(file_bytes))
        return pytesseract.image_to_string(image, lang='rus')
    except Exception as e:
        logger.exception(f"OCR ошибка: {e}")
        return ""


def normalize_name(name: str) -> list:
    return re.findall(r'[А-Яа-яЁё]+', name.lower())


def verify_signature(text: str, owner_name: str) -> dict:
    result = {
        "found": False,
        "confidence": 0.0,
        "message": "",
        "dates_found": [],
        "quorum_mentioned": False,
    }
    text_lower = text.lower()
    name_parts = normalize_name(owner_name)

    if name_parts and name_parts[0] in text_lower:
        result["found"] = True
        result["confidence"] = 0.7
        if len(name_parts) >= 3:
            initials = f"{name_parts[1]}.{name_parts[2]}"
            if initials in text_lower or f"{name_parts[1]}. {name_parts[2]}." in text_lower:
                result["confidence"] = 0.9
                result["message"] = "ФИО и инициалы найдены — подпись вероятна"
            else:
                result["message"] = "Фамилия найдена, инициалы не совпадают"
        else:
            result["message"] = "Фамилия найдена в тексте"
    else:
        result["message"] = "ФИО НЕ найдено — возможна подделка"

    result["dates_found"] = re.findall(r'\d{2}[.\-/]\d{2}[.\-/]\d{4}', text)
    result["quorum_mentioned"] = any(
        kw in text_lower for kw in ["кворум", "50%", "более половины"]
    )
    return result


def extract_owners_from_protocol(text: str) -> list:
    pattern = r'[А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.\s*[А-ЯЁ]\.'
    return list(set(re.findall(pattern, text)))