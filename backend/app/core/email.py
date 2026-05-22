"""
邮件发送服务

支持：
- 生产模式：通过 SMTP (aiosmtplib) 异步发送 HTML 邮件
- 开发模式：SMTP 未配置时打印重置链接到日志（不阻塞流程）
"""

import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.core.config import settings

logger = logging.getLogger(__name__)


async def send_email(
    to: str,
    subject: str,
    html_body: str,
    *,
    text_body: str | None = None,
) -> bool:
    """
    异步发送 HTML 邮件。

    SMTP 未配置时降级为日志输出（开发模式）。
    返回 True 表示发送成功，False 表示已降级或失败。
    """
    if not _smtp_configured():
        logger.warning(
            f"[EMAIL] SMTP 未配置，跳过发送。收件人: {to}\n"
            f"主题: {subject}\n"
            f"内容(前200字): {html_body[:200]}"
        )
        return False

    msg = MIMEMultipart("alternative")
    msg["From"] = settings.SMTP_FROM
    msg["To"] = to
    msg["Subject"] = subject

    if text_body:
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        import aiosmtplib

        await aiosmtplib.send(
            msg,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USER or None,
            password=settings.SMTP_PASSWORD or None,
            start_tls=settings.SMTP_USE_TLS,
        )
        logger.info(f"[EMAIL] 邮件已发送: {to} — {subject}")
        return True
    except Exception as e:
        logger.error(f"[EMAIL] 发送失败: {to} — {e}")
        return False


def _smtp_configured() -> bool:
    """检查 SMTP 是否已配置（区别于默认值）"""
    return bool(
        settings.SMTP_HOST
        and settings.SMTP_HOST != "smtp.example.com"
        and settings.SMTP_USER
        and settings.SMTP_PASSWORD
    )


def build_password_reset_email(reset_link: str, username: str = "") -> tuple[str, str]:
    """
    构建密码重置邮件的内容。

    返回 (html_body, text_body)。
    """
    subject = "PioneClaw - 密码重置"
    html = f"""
    <html>
    <body style="font-family: sans-serif; padding: 20px;">
        <h2>PioneClaw 密码重置</h2>
        <p>您好{(" " + username) if username else ""}，</p>
        <p>我们收到了您的密码重置请求。请点击以下链接重置密码：</p>
        <p>
            <a href="{reset_link}" style="
                background: #4f46e5;
                color: white;
                padding: 10px 20px;
                text-decoration: none;
                border-radius: 5px;
            ">重置密码</a>
        </p>
        <p>或者复制以下链接到浏览器：</p>
        <p><code>{reset_link}</code></p>
        <p>此链接 {settings.PASSWORD_RESET_EXPIRE_MINUTES} 分钟内有效。</p>
        <hr />
        <p style="color: #888; font-size: 12px;">
            如果您没有请求重置密码，请忽略此邮件。
        </p>
    </body>
    </html>
    """
    text = (
        f"PioneClaw 密码重置\n\n"
        f"您好{' ' + username if username else ''}，\n\n"
        f"请访问以下链接重置密码：\n{reset_link}\n\n"
        f"此链接 {settings.PASSWORD_RESET_EXPIRE_MINUTES} 分钟内有效。\n\n"
        f"如果您没有请求重置密码，请忽略此邮件。"
    )
    return subject, html, text
