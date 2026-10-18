"""微信渠道（腾讯 iLink Bot API，参考 Hermes `gateway/platforms/weixin.py` 移植）。

个人微信扫码绑定（无封号），长轮询收消息、带 context_token 发消息。
核心协议：https://ilinkai.weixin.qq.com
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import re
import secrets
import time
import uuid
from pathlib import Path
from typing import Dict, Optional

import aiohttp

from .base import Channel, MessageHandler
from ..splitter import split_reply

logger = logging.getLogger("friend-agent")

ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
CHANNEL_VERSION = "2.2.0"
ILINK_APP_ID = "bot"
ILINK_APP_CLIENT_VERSION = (2 << 16) | (2 << 8) | 0  # 2.2.0
EP_GET_UPDATES = "ilink/bot/getupdates"
EP_SEND_MESSAGE = "ilink/bot/sendmessage"
EP_GET_BOT_QR = "ilink/bot/get_bot_qrcode"
EP_GET_QR_STATUS = "ilink/bot/get_qrcode_status"
ITEM_TEXT = 1
MSG_TYPE_BOT = 2
MSG_STATE_FINISH = 2
LONG_POLL_TIMEOUT_MS = 35_000


def _headers(token: Optional[str], body: str) -> Dict[str, str]:
    uin = base64.b64encode(
        str(int.from_bytes(secrets.token_bytes(4), "big")).encode()).decode()
    return {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Content-Length": str(len(body.encode("utf-8"))),
        "X-WECHAT-UIN": uin,
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
        **({"Authorization": f"Bearer {token}"} if token else {}),
    }


def _extract_text(item_list) -> str:
    for item in item_list:
        if item.get("type") == ITEM_TEXT:
            return str((item.get("text_item") or {}).get("text") or "")
    return ""


def _thinking_delay(next_text: str) -> float:
    """根据下一句长度估算「思考时间」（秒）：短句快、长句慢，加随机抖动。

    模拟真人「发完一句，想一想下一句怎么说」的节奏。
    """
    n = len(next_text)
    if n <= 4:
        base = 1.2
    elif n <= 12:
        base = 2.2
    elif n <= 25:
        base = 3.2
    else:
        base = 4.2
    return base + random.uniform(0, 1.5)


class WeixinChannel(Channel):
    """个人微信 iLink Bot 渠道。"""

    name = "weixin"

    def __init__(self, data_dir: str = "data",
                 base_url: str = ILINK_BASE_URL,
                 account_id: str = "", token: str = ""):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._account_id = account_id
        self._token = token
        self._base_url = base_url
        self._context_tokens: Dict[str, str] = {}  # user_id -> context_token
        self._last_contact: str = ""  # 最近联系的用户，用于主动推送
        self._sync_buf = ""
        self._session: Optional[aiohttp.ClientSession] = None

    # ---- 凭据持久化 ----
    def _creds_path(self) -> Path:
        return self.data_dir / "weixin_account.json"

    def load_creds(self) -> bool:
        p = self._creds_path()
        if not p.exists():
            return False
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            self._account_id = data.get("account_id", "")
            self._token = data.get("token", "")
            self._base_url = data.get("base_url", ILINK_BASE_URL)
            self._last_contact = data.get("last_contact", "")  # 持久化最近联系人
            return bool(self._account_id and self._token)
        except Exception:
            return False

    def save_creds(self) -> None:
        self._creds_path().write_text(
            json.dumps({"account_id": self._account_id, "token": self._token,
                        "base_url": self._base_url,
                        "last_contact": self._last_contact},
                       ensure_ascii=False, indent=2),
            encoding="utf-8")

    @staticmethod
    def _render_qr(data: str) -> str:
        """渲染二维码为 ASCII 文本（用 ██ 块，适合 TUI 显示）。"""
        try:
            import qrcode as qrcode_lib
            q = qrcode_lib.QRCode()
            q.add_data(data)
            q.make(fit=True)
            matrix = q.get_matrix()
            return "\n".join(
                "".join("██" if cell else "  " for cell in row)
                for row in matrix
            )
        except Exception:
            return f"二维码链接：{data}"

    @property
    def is_logged_in(self) -> bool:
        return bool(self._account_id and self._token)

    # ---- HTTP 助手 ----
    def _session_or_raise(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _post(self, endpoint: str, payload: Dict,
                    token: Optional[str] = None, timeout_ms: int = 15_000) -> Dict:
        body = json.dumps({**payload, "base_info": {"channel_version": CHANNEL_VERSION}},
                          ensure_ascii=False, separators=(",", ":"))
        session = self._session_or_raise()
        async with session.post(f"{self._base_url.rstrip('/')}/{endpoint}",
                                data=body, headers=_headers(token, body),
                                timeout=aiohttp.ClientTimeout(total=timeout_ms / 1000)) as resp:
            raw = await resp.text()
            if not resp.ok:
                raise RuntimeError(f"iLink {endpoint} HTTP {resp.status}: {raw[:200]}")
            return json.loads(raw)

    async def _get(self, endpoint: str, timeout_ms: int = 15_000) -> Dict:
        headers = {"iLink-App-Id": ILINK_APP_ID,
                   "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION)}
        session = self._session_or_raise()
        async with session.get(f"{self._base_url.rstrip('/')}/{endpoint}",
                               headers=headers,
                               timeout=aiohttp.ClientTimeout(total=timeout_ms / 1000)) as resp:
            raw = await resp.text()
            if not resp.ok:
                raise RuntimeError(f"iLink {endpoint} HTTP {resp.status}: {raw[:200]}")
            return json.loads(raw)

    # ---- 扫码登录 ----
    async def qr_login(self, bot_type: str = "3", timeout_seconds: int = 480,
                       on_qr=None) -> bool:
        """生成二维码 → 等待微信扫码确认 → 保存凭据。

        on_qr: 可选回调，接收二维码 ASCII 文本（TUI 用），用于把二维码显示到界面。
        """
        qr = await self._get(f"{EP_GET_BOT_QR}?bot_type={bot_type}", timeout_ms=35_000)
        qrcode_value = str(qr.get("qrcode") or "")
        qrcode_url = str(qr.get("qrcode_img_content") or "")
        if not qrcode_value:
            logger.error("二维码响应缺少 qrcode")
            return False
        qr_text = self._render_qr(qrcode_url or qrcode_value)
        if on_qr:
            on_qr(qr_text)
        else:
            print("\n请使用微信扫描以下二维码：")
            print(qr_text)

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                status = await self._get(f"{EP_GET_QR_STATUS}?qrcode={qrcode_value}",
                                         timeout_ms=35_000)
            except Exception as exc:
                logger.warning("二维码轮询失败: %s", exc)
                await asyncio.sleep(1)
                continue
            st = str(status.get("status") or "wait")
            if st == "wait":
                print(".", end="", flush=True)
            elif st == "scaned":
                print("\n已扫码，请在微信里确认...")
            elif st == "confirmed":
                self._account_id = str(status.get("ilink_bot_id") or "")
                self._token = str(status.get("bot_token") or "")
                self._base_url = str(status.get("baseurl") or ILINK_BASE_URL)
                if not self._account_id or not self._token:
                    logger.error("扫码确认但凭据不完整")
                    return False
                self.save_creds()
                print(f"\n微信连接成功，account_id={self._account_id}")
                return True
            elif st == "expired":
                print("\n二维码已过期，请重新运行登录。")
                return False
            await asyncio.sleep(1)
        print("\n微信登录超时。")
        return False

    # ---- 长轮询收消息 ----
    async def _poll(self) -> list:
        try:
            resp = await self._post(EP_GET_UPDATES,
                                    {"get_updates_buf": self._sync_buf},
                                    token=self._token,
                                    timeout_ms=LONG_POLL_TIMEOUT_MS)
        except asyncio.TimeoutError:
            return []
        if not isinstance(resp, dict):
            return []
        msgs = resp.get("msgs") or []
        new_buf = str(resp.get("get_updates_buf") or "")
        if new_buf and new_buf != self._sync_buf:
            self._sync_buf = new_buf
        return msgs

    # ---- 发消息 ----
    async def send(self, user_id: str, text: str) -> None:
        if not text.strip():
            return
        # 主动推送时 user_id 为空，发给最近联系的人
        target = user_id or self._last_contact
        if not target:
            logger.warning("微信主动推送失败：没有可发送的联系人")
            return
        context_token = self._context_tokens.get(target)
        # 拆成多条短消息，像真人一句句发（先想一下，再逐条带间隔发送）
        msgs = split_reply(text)
        if not msgs:
            return
        logger.info("微信发送：target=%s has_token=%s 共%d条",
                    target[:8] if target else "", bool(context_token), len(msgs))
        await asyncio.sleep(random.uniform(1.0, 2.0))  # 看到消息，先想一下
        for i, msg in enumerate(msgs):
            await self._send_text(target, msg, context_token,
                                  retry_without_token=bool(context_token))
            if i < len(msgs) - 1:
                # 发完这句，再「思考」下一句怎么说（按长度 + 随机）
                await asyncio.sleep(_thinking_delay(msgs[i + 1]))

    async def _send_text(self, to: str, text: str, context_token: Optional[str],
                         retry_without_token: bool) -> None:
        item_list = [{"type": ITEM_TEXT, "text_item": {"text": text}}]
        message = {
            "from_user_id": "", "to_user_id": to,
            "client_id": str(uuid.uuid4())[:16],
            "message_type": MSG_TYPE_BOT, "message_state": MSG_STATE_FINISH,
            "item_list": item_list,
        }
        if context_token:
            message["context_token"] = context_token
        resp = await self._post(EP_SEND_MESSAGE, {"msg": message}, token=self._token)
        ret, errcode = resp.get("ret"), resp.get("errcode")
        if (ret is not None and ret != 0) or (errcode is not None and errcode != 0):
            errmsg = resp.get("errmsg") or resp.get("msg") or "unknown"
            # session 过期 → 降级重试一次（不带 context_token）
            if retry_without_token and (ret in (-2, -14) or errcode in (-2, -14)):
                logger.warning("微信 session 过期，尝试无 token 重发：ret=%s errcode=%s", ret, errcode)
                self._context_tokens.pop(to, None)
                await self._send_text(to, text, None, retry_without_token=False)
                return
            logger.warning("微信发送失败: ret=%s errcode=%s errmsg=%s to=%s",
                           ret, errcode, errmsg, to[:8] if to else "")
        else:
            logger.info("微信发送成功: to=%s", to[:8] if to else "")

    # ---- 渠道接口 ----
    async def start(self, handler: MessageHandler,
                    stream_handler=None) -> None:
        if not self.is_logged_in:
            raise RuntimeError("微信未登录，请先调用 qr_login() 扫码绑定")
        logger.info("微信渠道启动（长轮询），account_id=%s", self._account_id[:8])
        while True:
            try:
                msgs = await self._poll()
                for message in msgs:
                    sender = str(message.get("from_user_id") or "").strip()
                    text = _extract_text(message.get("item_list") or [])
                    ct = str(message.get("context_token") or "").strip()
                    if ct:
                        self._context_tokens[sender] = ct
                    if sender:
                        if sender != self._last_contact:
                            self._last_contact = sender
                            self.save_creds()  # 持久化最近联系人，重启后主动推送仍能送达
                    if not sender or not text:
                        continue
                    logger.info("收到微信消息 from=%s: %s", sender[:8], text[:50])
                    if stream_handler:
                        # 流式逐句回复：每生成完一句就发，边想边说
                        await self._reply_stream(sender, text, stream_handler,
                                                 self._context_tokens.get(sender))
                    else:
                        reply = await handler(sender, text)
                        if reply:
                            await self.send(sender, reply)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("微信长轮询异常: %s", exc)
                await asyncio.sleep(3)

    async def _reply_stream(self, user_id: str, original_text: str,
                            stream_handler, context_token) -> None:
        """流式逐句回复：LLM 每生成完一句就发送，发送时它继续想下一句。"""
        buf = ""
        sent_any = False

        async def flush(sentence: str) -> None:
            nonlocal sent_any
            sentence = sentence.strip()
            if not sentence:
                return
            if not sent_any:
                await asyncio.sleep(random.uniform(1.0, 2.0))  # 看到消息先想一下
                sent_any = True
            await self._send_text(user_id, sentence, context_token,
                                  retry_without_token=bool(context_token))
            await asyncio.sleep(_thinking_delay(sentence))

        try:
            async for chunk in stream_handler(user_id, original_text):
                buf += chunk
                # 找最近的句子边界（句末标点 或 换行），完整句子立即发送
                while True:
                    m_punct = re.search(r"[。！？…!?]", buf)
                    nl = buf.find("\n")
                    cuts = []
                    if m_punct:
                        cuts.append(m_punct.end())
                    if nl >= 0:
                        cuts.append(nl + 1)
                    if not cuts:
                        break
                    cut = min(cuts)
                    sentence = buf[:cut].strip()
                    buf = buf[cut:].lstrip("\n")
                    await flush(sentence)
        except Exception as exc:
            logger.error("流式逐句回复失败: %s", exc)
        # 流结束，发送剩余内容
        await flush(buf)
