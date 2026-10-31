import asyncio
import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import get_settings

settings = get_settings()


def _send_sync(to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.EMAIL_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    context = ssl.create_default_context()
    if settings.SMTP_USE_SSL:
        server = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, context=context, timeout=15)
    else:
        server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15)
    with server:
        if not settings.SMTP_USE_SSL:
            server.starttls(context=context)
        if settings.SMTP_USERNAME:
            server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        server.send_message(msg)


async def send_email(to: str, subject: str, body: str) -> None:
    """
    Never raises: callers run this in the background after answering the request, and a mail
    failure must not change what the request looked like from outside.
    """
    if not settings.SMTP_HOST:
        print(f"[email] SMTP_HOST is not set, so this was not sent.\n  To: {to}\n  Subject: {subject}\n{body}\n",
              flush=True)
        return
    try:
        await asyncio.to_thread(_send_sync, to, subject, body)
    except Exception as e:
        print(f"[email] could not send '{subject}' to {to}: {type(e).__name__}: {e}", flush=True)
