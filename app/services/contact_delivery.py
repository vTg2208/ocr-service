"""Configured SMTP/Twilio delivery; terminal codes are development only."""

from email.message import EmailMessage
import logging
import re
import smtplib
import ssl

import httpx

from app.config import get_settings


class DeliveryUnavailable(RuntimeError):
    pass


def delivery_method(channel):
    s = get_settings()
    backend = s.otp_email_backend if channel == "email" else s.otp_sms_backend
    if backend == "terminal":
        if s.environment != "development":
            raise DeliveryUnavailable("Configure a real contact delivery provider.")
        return "development_terminal"
    return backend


def deliver_code(*, channel, target, code, account, method):
    s = get_settings()
    body = f"Your AranyaSetu verification code is {code}. It expires in five minutes. Do not share it."
    try:
        if method == "development_terminal":
            if s.environment != "development":
                raise DeliveryUnavailable("Terminal delivery is disabled.")
            logging.getLogger("app.contact_otp").warning("DEV OTP | account=%s | channel=%s | target=%s | code=%s | expires in 5 minutes", account, channel, target, code)
        elif method == "smtp":
            if not s.smtp_host or not s.smtp_sender:
                raise DeliveryUnavailable("SMTP host and sender are required.")
            message = EmailMessage()
            message["Subject"] = "AranyaSetu contact verification"
            message["From"], message["To"] = s.smtp_sender, target
            message.set_content(body)
            context = ssl.create_default_context()
            factory = smtplib.SMTP_SSL if s.smtp_tls_mode == "ssl" else smtplib.SMTP
            kwargs = {"context": context} if s.smtp_tls_mode == "ssl" else {}
            with factory(s.smtp_host, s.smtp_port, timeout=15, **kwargs) as client:
                if s.smtp_tls_mode == "starttls":
                    client.starttls(context=context)
                elif s.smtp_tls_mode == "plain" and s.environment != "development":
                    raise DeliveryUnavailable("SMTP TLS is required.")
                if s.smtp_username:
                    client.login(s.smtp_username, s.smtp_password.get_secret_value())
                refused = client.send_message(message)
                if refused:
                    raise DeliveryUnavailable("Email delivery was refused.")
        elif method == "twilio":
            if not re.fullmatch(r"AC[0-9a-fA-F]{32}", s.twilio_account_sid) or not s.twilio_auth_token.get_secret_value() or not s.twilio_sender:
                raise DeliveryUnavailable("Twilio credentials and sender are required.")
            if not re.fullmatch(r"\+[1-9][0-9]{7,14}", target):
                raise DeliveryUnavailable("SMS requires a saved international E.164 phone number.")
            response = httpx.post(f"https://api.twilio.com/2010-04-01/Accounts/{s.twilio_account_sid}/Messages.json",
                auth=(s.twilio_account_sid, s.twilio_auth_token.get_secret_value()),
                data={"To": target, "From": s.twilio_sender, "Body": body}, timeout=15, follow_redirects=False)
            response.raise_for_status()
        else:
            raise DeliveryUnavailable("Unsupported contact provider.")
    except DeliveryUnavailable:
        raise
    except Exception as error:
        # Provider errors can contain credentials, contact values or the code.
        raise DeliveryUnavailable("The delivery provider is unavailable; retry later.") from error
