"""Email sending via Resend."""

import os

try:
    import resend

    resend.api_key = os.getenv("RESEND_API_KEY", "")
    _resend_available = True
except ImportError:
    _resend_available = False


def send_verification_code(to_email: str, code: str, purpose: str = "验证") -> bool:
    """Send a verification code email via Resend.

    Raises RuntimeError if unconfigured or resend SDK not installed.
    """
    if not _resend_available:
        raise RuntimeError("未安装 resend SDK，请执行: pip install resend")
    if not resend.api_key:
        print("[EmailService] WARNING: RESEND_API_KEY 未配置，验证码无法发送")
        raise RuntimeError(
            "邮件服务未配置。请在 .env 中设置 RESEND_API_KEY，"
            "或联系管理员启用邮件功能。"
        )
    from_email = os.getenv("RESEND_FROM_EMAIL", "noreply@resend.dev")
    resend.Emails.send({
        "from": from_email,
        "to": [to_email],
        "subject": f"CharSim {purpose}验证码: {code}",
        "html": f"<p>你的验证码是 <strong>{code}</strong>，5分钟内有效。</p>",
    })
    return True
