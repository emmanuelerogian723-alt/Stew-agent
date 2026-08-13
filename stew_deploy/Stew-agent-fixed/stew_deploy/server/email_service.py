"""
S.T.E.W Email Service — Welcome emails + Password reset.
Uses SMTP (works with Gmail App Password, SendGrid, Mailgun, Brevo).
Falls back gracefully if SMTP is not configured.
"""
import asyncio
import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from server.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

STEW_BLUE = "#00b4d8"
STEW_DARK = "#0d1117"
STEW_ACCENT = "#7c3aed"


def _build_html(title: str, body_html: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title}</title>
</head>
<body style="margin:0;padding:0;background:{STEW_DARK};font-family:'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:{STEW_DARK};padding:40px 0;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0"
               style="background:#161b22;border-radius:16px;overflow:hidden;border:1px solid #30363d;">
          <!-- Header -->
          <tr>
            <td style="background:linear-gradient(135deg,{STEW_ACCENT},{STEW_BLUE});padding:32px;text-align:center;">
              <h1 style="margin:0;color:#ffffff;font-size:28px;font-weight:800;letter-spacing:2px;">
                S.T.E.W
              </h1>
              <p style="margin:4px 0 0;color:rgba(255,255,255,0.85);font-size:13px;letter-spacing:1px;">
                Smart Thinking Executive Worker
              </p>
            </td>
          </tr>
          <!-- Body -->
          <tr>
            <td style="padding:36px 40px;color:#e6edf3;font-size:15px;line-height:1.7;">
              {body_html}
            </td>
          </tr>
          <!-- Footer -->
          <tr>
            <td style="background:#0d1117;padding:24px 40px;border-top:1px solid #21262d;text-align:center;">
              <p style="margin:0;color:#8b949e;font-size:12px;">
                © 2026 S.T.E.W Agent · Powered by AI
              </p>
              <p style="margin:8px 0 0;color:#8b949e;font-size:11px;">
                You received this email because you signed up at stew-agent.onrender.com
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _send_smtp(to_email: str, subject: str, html: str, text: str) -> bool:
    """Send via SMTP. Returns True on success."""
    if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        logger.warning("Email not sent — SMTP_USER/SMTP_PASSWORD not set")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_FROM_EMAIL or settings.SMTP_USER}>"
        msg["To"] = to_email
        msg.attach(MIMEText(text, "plain"))
        msg.attach(MIMEText(html, "html"))

        context = ssl.create_default_context()
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.ehlo()
            server.starttls(context=context)
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_USER, to_email, msg.as_string())
        logger.info(f"Email sent to {to_email}: {subject}")
        return True
    except Exception as e:
        logger.error(f"SMTP error sending to {to_email}: {e}")
        return False


async def send_welcome_email(to_email: str, name: str, api_key: str, plan: str) -> bool:
    """Send welcome email to new developer signups."""
    plan_badge_color = {"free": "#6e7681", "pro": STEW_BLUE, "business": STEW_ACCENT, "enterprise": "#f0a500"}
    badge_color = plan_badge_color.get(plan, "#6e7681")

    base_url = settings.APP_BASE_URL or "https://stew-agent.onrender.com"

    body = f"""
<h2 style="margin:0 0 8px;color:#ffffff;font-size:22px;">Welcome to S.T.E.W, {name}! 🤖</h2>
<p style="margin:0 0 24px;color:#8b949e;font-size:14px;">Your developer account is ready.</p>

<div style="background:#0d1117;border:1px solid #30363d;border-radius:12px;padding:24px;margin-bottom:24px;">
  <p style="margin:0 0 8px;color:#8b949e;font-size:12px;text-transform:uppercase;letter-spacing:1px;">Your API Key</p>
  <p style="margin:0;color:{STEW_BLUE};font-family:monospace;font-size:14px;word-break:break-all;">{api_key}</p>
</div>

<div style="display:inline-block;background:{badge_color};color:#fff;padding:4px 14px;border-radius:20px;font-size:12px;font-weight:700;text-transform:uppercase;margin-bottom:24px;">
  {plan.upper()} PLAN
</div>

<h3 style="color:#ffffff;font-size:16px;margin:0 0 12px;">Quick Start</h3>
<div style="background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:16px;margin-bottom:24px;">
<pre style="margin:0;color:#79c0ff;font-size:12px;overflow-x:auto;">curl -X POST {base_url}/chat \\
  -H "Content-Type: application/json" \\
  -d '{{"message":"Hello S.T.E.W","api_key":"{api_key}"}}'</pre>
</div>

<h3 style="color:#ffffff;font-size:16px;margin:0 0 12px;">What S.T.E.W Can Do For You</h3>
<ul style="color:#e6edf3;padding-left:20px;margin:0 0 24px;">
  <li style="margin-bottom:8px;">🌐 Real web search + autonomous browsing</li>
  <li style="margin-bottom:8px;">📄 Generate PDF, Word, Excel, PowerPoint files</li>
  <li style="margin-bottom:8px;">🤖 60+ built-in skills (finance, data, code review, etc.)</li>
  <li style="margin-bottom:8px;">💬 Connect via Telegram bot</li>
  <li style="margin-bottom:8px;">🔗 Integrate with any external API</li>
</ul>

<div style="text-align:center;margin-top:32px;">
  <a href="{base_url}/docs"
     style="background:linear-gradient(135deg,{STEW_ACCENT},{STEW_BLUE});color:#fff;
            padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:700;
            font-size:15px;display:inline-block;">
    View API Documentation →
  </a>
</div>

<p style="margin:32px 0 0;color:#8b949e;font-size:13px;">
  Keep your API key safe — it gives full access to your S.T.E.W account.
  If you need help, reply to this email or check the docs.
</p>
"""

    text = f"""Welcome to S.T.E.W, {name}!

Your API Key: {api_key}
Plan: {plan.upper()}

Quick start:
curl -X POST {base_url}/chat -H "Content-Type: application/json" -d '{{"message":"Hello","api_key":"{api_key}"}}'

API Docs: {base_url}/docs

Keep your API key safe!
— The S.T.E.W Team
"""

    html = _build_html("Welcome to S.T.E.W", body)
    return await asyncio.get_event_loop().run_in_executor(
        None, _send_smtp, to_email, "🤖 Welcome to S.T.E.W — Your API Key is Ready", html, text
    )


async def send_password_reset_email(to_email: str, name: str, reset_token: str) -> bool:
    """Send password reset link email."""
    base_url = settings.APP_BASE_URL or "https://stew-agent.onrender.com"
    reset_url = f"{base_url}/reset-password?token={reset_token}"

    body = f"""
<h2 style="margin:0 0 8px;color:#ffffff;font-size:22px;">Reset Your Password</h2>
<p style="margin:0 0 24px;color:#8b949e;">Hi {name}, we received a request to reset your S.T.E.W password.</p>

<div style="background:#161b22;border:1px solid #f85149;border-radius:12px;padding:20px;margin-bottom:24px;">
  <p style="margin:0;color:#f85149;font-size:13px;">
    ⚠️ This link expires in <strong>1 hour</strong>. If you didn't request a reset, ignore this email.
  </p>
</div>

<div style="text-align:center;margin:32px 0;">
  <a href="{reset_url}"
     style="background:linear-gradient(135deg,#f85149,{STEW_ACCENT});color:#fff;
            padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:700;
            font-size:15px;display:inline-block;">
    Reset My Password →
  </a>
</div>

<p style="color:#8b949e;font-size:13px;margin-top:24px;">
  Or copy this link into your browser:<br>
  <span style="color:{STEW_BLUE};font-family:monospace;font-size:12px;word-break:break-all;">{reset_url}</span>
</p>

<p style="color:#8b949e;font-size:13px;margin-top:24px;">
  If you didn't request this, your account is safe — no changes were made.
</p>
"""

    text = f"""Reset Your S.T.E.W Password

Hi {name},

Click the link below to reset your password (expires in 1 hour):
{reset_url}

If you didn't request this, ignore this email — your account is safe.

— The S.T.E.W Team
"""

    html = _build_html("Reset Your S.T.E.W Password", body)
    return await asyncio.get_event_loop().run_in_executor(
        None, _send_smtp, to_email, "🔐 Reset Your S.T.E.W Password", html, text
    )


async def send_password_changed_email(to_email: str, name: str) -> bool:
    """Confirm password was successfully changed."""
    body = f"""
<h2 style="margin:0 0 8px;color:#ffffff;">Password Changed Successfully ✅</h2>
<p style="margin:0 0 24px;color:#8b949e;">Hi {name}, your S.T.E.W password was just changed.</p>

<div style="background:#161b22;border:1px solid #3fb950;border-radius:12px;padding:20px;">
  <p style="margin:0;color:#3fb950;font-size:14px;">
    If you made this change, no action is needed. Your account is secure.
  </p>
</div>

<p style="color:#8b949e;font-size:13px;margin-top:24px;">
  If you did NOT make this change, contact us immediately by replying to this email.
</p>
"""
    text = f"Hi {name}, your S.T.E.W password was changed. If this wasn't you, contact us immediately."
    html = _build_html("Password Changed — S.T.E.W", body)
    return await asyncio.get_event_loop().run_in_executor(
        None, _send_smtp, to_email, "✅ S.T.E.W Password Changed", html, text
    )
