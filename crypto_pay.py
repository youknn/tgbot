"""Thin async client for Crypto Pay API + webhook signature verification.
Docs: https://help.crypt.bot/crypto-pay-api"""
import hmac
import hashlib
import aiohttp


class CryptoPay:
    def __init__(self, token: str, api: str = "https://pay.crypt.bot"):
        self.token = token
        self.api = api.rstrip("/")
        self._secret = hashlib.sha256(token.encode()).digest()

    async def _call(self, method: str, **params) -> dict:
        url = f"{self.api}/api/{method}"
        headers = {"Crypto-Pay-API-Token": self.token}
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=params, headers=headers, timeout=20) as r:
                data = await r.json()
                if not data.get("ok"):
                    raise RuntimeError(f"CryptoPay {method} failed: {data}")
                return data["result"]

    async def create_invoice(self, amount: float, asset: str = "USDT",
                             description: str = "", payload: str = "",
                             expires_in: int = 1800) -> dict:
        return await self._call(
            "createInvoice",
            asset=asset,
            amount=f"{amount:.6f}".rstrip("0").rstrip(".") or "0",
            description=description[:1024],
            payload=payload[:4096],
            expires_in=expires_in,
            allow_anonymous=False,
            allow_comments=False,
        )

    async def get_me(self) -> dict:
        return await self._call("getMe")

    def verify_webhook(self, body: bytes, signature: str | None) -> bool:
        if not signature:
            return False
        calc = hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(calc, signature)
