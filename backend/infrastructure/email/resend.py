"""Minimal, SSRF-safe Resend API client for transactional verification emails."""

from __future__ import annotations

import html
import os
from dataclasses import dataclass

import httpx

RESEND_API_URL = "https://api.resend.com/emails"


@dataclass(frozen=True)
class EmailSendResult:
    ok: bool
    message_id: str | None = None
    error_code: str | None = None
    detail: str | None = None


def _build_verification_html(title: str, intro: str, code: str, expires_minutes: int) -> str:
    safe_title = html.escape(title)
    safe_intro = html.escape(intro)
    safe_code = html.escape(code)
    return (
        "<div style='font-family:-apple-system,BlinkMacSystemFont,Segoe UI,PingFang SC,Microsoft YaHei,sans-serif;"
        "max-width:480px;margin:24px auto;padding:28px;border:1px solid #e2e8f0;border-radius:12px;"
        "background:#ffffff;color:#0f172a;'>"
        f"<h2 style='margin:0 0 12px;font-size:18px;color:#0f172a;'>{safe_title}</h2>"
        f"<p style='margin:0 0 20px;font-size:14px;line-height:1.6;color:#475569;'>{safe_intro}</p>"
        "<div style='padding:16px;border-radius:8px;background:#f1f5f9;text-align:center;"
        "font-size:28px;font-weight:700;letter-spacing:6px;color:#0f172a;font-family:monospace;'>"
        f"{safe_code}</div>"
        f"<p style='margin:18px 0 0;font-size:12px;color:#64748b;'>验证码 {expires_minutes} 分钟内有效。"
        "如非本人操作，请直接忽略此邮件。</p>"
        "</div>"
    )


async def send_resend_email(
    *,
    api_key: str,
    from_email: str,
    from_name: str,
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> EmailSendResult:
    """Send one email via Resend REST API (https://api.resend.com/emails)."""

    clean_key = (api_key or "").strip()
    clean_from = (from_email or "").strip()
    clean_to = (to_email or "").strip()
    clean_name = (from_name or "").strip()
    if not clean_key or not clean_from:
        return EmailSendResult(ok=False, error_code="email_provider_unconfigured")
    sender = f"{clean_name} <{clean_from}>" if clean_name and "<" not in clean_from else clean_from
    endpoint = os.environ.get("ZHOUXU_RESEND_ENDPOINT", RESEND_API_URL).strip() or RESEND_API_URL

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(12.0),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            response = await client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {clean_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": sender,
                    "to": [clean_to],
                    "subject": subject,
                    "html": html_body,
                    "text": text_body,
                },
            )
    except httpx.TimeoutException:
        return EmailSendResult(ok=False, error_code="email_send_timeout")
    except httpx.RequestError:
        return EmailSendResult(ok=False, error_code="email_send_unreachable")

    detail: str | None = None
    if not response.is_success:
        try:
            body = response.json()
            if isinstance(body, dict) and isinstance(body.get("message"), str):
                detail = body["message"][:200]
        except ValueError:
            pass

    if response.status_code in {401, 403}:
        return EmailSendResult(ok=False, error_code="email_auth_failed", detail=detail)
    if response.status_code == 429:
        return EmailSendResult(
            ok=False, error_code="email_provider_rate_limited", detail=detail
        )
    if not response.is_success:
        return EmailSendResult(ok=False, error_code="email_send_rejected", detail=detail)

    try:
        payload = response.json()
        msg_id = payload.get("id") if isinstance(payload, dict) else None
    except ValueError:
        msg_id = None
    return EmailSendResult(ok=True, message_id=str(msg_id) if msg_id else None)


async def send_verification_email(
    *,
    api_key: str,
    from_email: str,
    from_name: str,
    to_email: str,
    purpose: str,
    code: str,
    expires_minutes: int = 5,
    transport: httpx.AsyncBaseTransport | None = None,
) -> EmailSendResult:
    if purpose == "reset_password":
        title = "舟序 Zhouxu · 重置密码验证码"
        intro = "你正在申请重置舟序登录密码，请在页面中输入以下 6 位验证码："
    else:
        title = "舟序 Zhouxu · 注册邮箱验证"
        intro = "欢迎注册舟序。请在注册页面中输入以下 6 位邮箱验证码："
    text_body = f"{title}\n\n{intro}\n\n验证码：{code}\n有效期：{expires_minutes} 分钟。\n如非本人操作请忽略。"
    html_body = _build_verification_html(title, intro, code, expires_minutes)
    return await send_resend_email(
        api_key=api_key,
        from_email=from_email,
        from_name=from_name,
        to_email=to_email,
        subject=title,
        html_body=html_body,
        text_body=text_body,
        transport=transport,
    )


__all__ = [
    "EmailSendResult",
    "send_resend_email",
    "send_verification_email",
]
