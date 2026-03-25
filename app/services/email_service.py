import smtplib
from email.message import EmailMessage
from typing import Any, Dict
import httpx
from pydantic import EmailStr

async def send_email(config: Dict[str, Any], recipient: str, subject: str, body: str):
    email_type = config.get("type")
    
    if email_type == "smtp":
        return await send_smtp_email(config, recipient, subject, body)
    elif email_type == "resend":
        return await send_resend_email(config, recipient, subject, body)
    else:
        raise ValueError(f"Unsupported email provider type: {email_type}")

async def send_smtp_email(config: Dict[str, Any], recipient: str, subject: str, body: str):
    msg = EmailMessage()
    msg.set_content(body)
    msg["Subject"] = subject
    msg["From"] = config["from_email"]
    msg["To"] = recipient

    host = config["host"]
    port = config["port"]
    user = config["user"]
    password = config["password"]
    use_tls = config.get("use_tls", True)

    try:
        if use_tls:
            context = smtplib.SSLContext(smtplib.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = smtplib.CERT_NONE
            
            with smtplib.SMTP(host, port) as server:
                server.starttls(context=context)
                server.login(user, password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(host, port) as server:
                server.login(user, password)
                server.send_message(msg)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

async def send_resend_email(config: Dict[str, Any], recipient: str, subject: str, body: str):
    api_key = config["api_key"]
    from_email = config["from_email"]
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": from_email,
                "to": recipient,
                "subject": subject,
                "text": body,
            },
        )
        if response.status_code in (200, 201):
            return {"ok": True}
        else:
            return {"ok": False, "error": response.text}
