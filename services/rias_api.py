import aiohttp
import logging
from config import RIAS_API_BASE, RIAS_API_TOKEN

logger = logging.getLogger(__name__)

# ВНИМАНИЕ: этот клиент НЕ умеет отправлять обращения гражданина.
# /appeals в API РИАС — импорт обращений, которые УК уже получила
# (AppealImportForm: organization_id, executor_id, applicant_org_guid).
# Токен выдаётся только зарегистрированной организации (УК/РСО).
# Подача обращений гражданина возможна лишь через мини-приложение
# «Госуслуги Дом» — см. services/uk_delivery.py.


class RIASClient:
    """Клиент РИАС ЖКХ REST API v2.0.
    Авторизация: access-token в параметре URL.
    База: https://api.rias-gkh.ru/v2.0/
    Тест: https://api.sit1.rucode.org/v2.0/
    """

    def __init__(self):
        self.base = RIAS_API_BASE
        self.token = RIAS_API_TOKEN
        self.session = None

    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()

    def _url(self, path: str, **params) -> str:
        params["access-token"] = self.token
        query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        return f"{self.base}{path}?{query}"

    async def _get(self, path: str, **params) -> dict:
        await self._ensure_session()
        url = self._url(path, **params)
        async with self.session.get(url) as resp:
            if resp.status == 401:
                logger.error("РИАС: неверный access-token")
                return {"error": "unauthorized"}
            if resp.status == 400:
                return {"error": "bad_request", "details": await resp.json()}
            return await resp.json()

    async def _post(self, path: str, payload: dict, **params) -> dict:
        await self._ensure_session()
        url = self._url(path, **params)
        headers = {"Content-Type": "application/json"}
        async with self.session.post(url, json=payload, headers=headers) as resp:
            if resp.status == 401:
                return {"error": "unauthorized"}
            if resp.status == 400:
                return {"error": "bad_request", "details": await resp.json()}
            return await resp.json()

    # ===== ОБЪЕКТЫ ЖИЛФОНДА =====
    async def get_houses(self, offset: int = 0, limit: int = 50, fields: str = None):
        return await self._get("/houses", offset=offset, limit=limit, fields=fields)

    # ===== ЛИЦЕВЫЕ СЧЕТА =====
    async def get_accounts(self, offset: int = 0, limit: int = 50,
                            fields: str = None, expand: str = None):
        """Коллекция лицевых счетов.
        Пример: ?fields=id,number&expand=meteringDevices
        """
        return await self._get(
            "/accounts", offset=offset, limit=limit,
            fields=fields, expand=expand
        )

    async def get_account(self, account_id: int):
        return await self._get(f"/accounts/{account_id}")

    # ===== ПРИБОРЫ УЧЁТА =====
    async def get_metering_devices(self, offset: int = 0, limit: int = 50):
        return await self._get("/metering-devices", offset=offset, limit=limit)

    async def get_metering_device(self, device_id: int):
        return await self._get(f"/metering-devices/{device_id}")

    # ===== ПОКАЗАНИЯ ПУ =====
    async def get_readings(self, offset: int = 0, limit: int = 50):
        return await self._get("/metering-device-readings", offset=offset, limit=limit)

    async def get_reading(self, reading_id: int):
        return await self._get(f"/metering-device-readings/{reading_id}")

    # ===== ПЛАТЁЖНЫЕ ДОКУМЕНТЫ =====
    async def get_payment_documents(self, offset: int = 0, limit: int = 50):
        return await self._get("/payment-documents", offset=offset, limit=limit)

    async def get_payment_document(self, document_id: int):
        return await self._get(f"/payment-documents/{document_id}")

    # ===== ОБРАЩЕНИЯ (только чтение) =====
    # create_appeal() здесь сознательно удалён: обращения гражданина
    # через этот API отправить нельзя, см. шапку модуля.
    async def get_appeals(self, offset: int = 0, limit: int = 50):
        return await self._get("/appeals", offset=offset, limit=limit)

    async def get_appeal(self, request_id: int):
        return await self._get(f"/appeals/{request_id}")

    # ===== ЗАДОЛЖЕННОСТИ =====
    async def get_debt_requests(self, offset: int = 0, limit: int = 50):
        return await self._get("/debt-requests", offset=offset, limit=limit)

    async def get_debts(self, offset: int = 0, limit: int = 50):
        return await self._get("/debts", offset=offset, limit=limit)

    # ===== ДОГОВОРЫ =====
    async def get_management_contracts(self, offset: int = 0, limit: int = 50):
        return await self._get("/management-contracts", offset=offset, limit=limit)

    async def get_resource_supply_contracts(self, offset: int = 0, limit: int = 50):
        return await self._get("/resource-supply-contracts", offset=offset, limit=limit)

    # ===== СПРАВОЧНИКИ НСИ =====
    async def get_nsi(self, registry_number: str = None):
        if registry_number:
            return await self._get(f"/nsi/{registry_number}")
        return await self._get("/nsi")

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


rias = RIASClient()