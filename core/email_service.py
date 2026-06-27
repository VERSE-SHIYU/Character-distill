"""Email sending: SMTP (primary) or Resend (fallback)."""

import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ---- Resend (optional fallback) ----
try:
    import resend as _resend_mod

    _resend_mod.api_key = os.getenv("RESEND_API_KEY", "")
    _resend_available = bool(_resend_mod.api_key)
except ImportError:
    _resend_available = False
    _resend_mod = None  # type: ignore


def _send_via_smtp(to_email: str, subject: str, html: str) -> None:
    host = os.getenv("SMTP_HOST", "")
    port = int(os.getenv("SMTP_PORT", "465"))
    user = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASSWORD", "")
    from_addr = os.getenv("SMTP_FROM", user)

    if not all([host, user, password]):
        raise RuntimeError("SMTP 未配置，请在 .env 中设置 SMTP_HOST / SMTP_USER / SMTP_PASSWORD")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_email
    msg.attach(MIMEText(html, "html", "utf-8"))

    ctx = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=10) as s:
                s.login(user, password)
                s.sendmail(from_addr, [to_email], msg.as_bytes())
        else:  # 587 STARTTLS
            with smtplib.SMTP(host, port, timeout=10) as s:
                s.ehlo()
                s.starttls(context=ctx)
                s.login(user, password)
                s.sendmail(from_addr, [to_email], msg.as_bytes())
    except Exception as e:
        raise RuntimeError(f"SMTP 发送失败: {e}") from e


def _send_via_resend(to_email: str, subject: str, html: str) -> None:
    if not _resend_available:
        raise RuntimeError("Resend 未配置")
    from_email = os.getenv("RESEND_FROM_EMAIL", "")
    if not from_email:
        raise RuntimeError("RESEND_FROM_EMAIL 未配置")
    try:
        _resend_mod.Emails.send({
            "from": from_email,
            "to": [to_email],
            "subject": subject,
            "html": html,
        })
    except Exception as e:
        raise RuntimeError(f"Resend 发送失败: {e}") from e


def send_verification_code(to_email: str, code: str, purpose: str = "验证") -> bool:
    """Send verification code. Raises RuntimeError on failure."""
    subject = f"CharSim {purpose}验证码: {code}"
    html = f"<p>你的{purpose}验证码是 <strong>{code}</strong>，5 分钟内有效。请勿泄露。</p>"

    if _resend_available:
        _send_via_resend(to_email, subject, html)
    elif all([os.getenv("SMTP_HOST"), os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD")]):
        _send_via_smtp(to_email, subject, html)
    else:
        raise RuntimeError("邮件服务未配置，请设置 RESEND_API_KEY 或 SMTP_HOST/SMTP_USER/SMTP_PASSWORD")

    return True
