"""
MailService
------------
Sends email via SMTP, configured from the project's .env file. Also supports
a 'log' driver that writes emails to a local file instead of actually sending
them - handy for local development so you don't accidentally email real
addresses while testing.

Project layout assumed - same as DatabaseConnection:
    project/
        .env
        core/
            services/
                MailService.py   <- this file

Expected .env keys
-------------------
MAIL_DRIVER=smtp | log                 (default: smtp)

smtp:
    MAIL_HOST
    MAIL_PORT               (default depends on MAIL_ENCRYPTION: 465/ssl, 587/tls, 25/none)
    MAIL_USERNAME
    MAIL_PASSWORD
    MAIL_ENCRYPTION=tls | ssl | none   (default: tls)

log (writes to a file instead of sending):
    MAIL_LOG_PATH           (default: <project_root>/storage/logs/mail.log)

common, both drivers:
    MAIL_FROM_ADDRESS
    MAIL_FROM_NAME

Usage
-----
    from core.services.MailService import MailService

    mailer = MailService()

    mailer.send(
        to="someone@example.com",
        subject="Welcome!",
        body="Plain text body",
        html="<p>HTML body</p>",
    )

    # multiple recipients, cc/bcc, attachments, explicit sender
    mailer.send(
        to=["a@example.com", "b@example.com"],
        cc="manager@example.com",
        bcc="audit@example.com",
        subject="Report",
        body="See attached.",
        attachments=["/path/to/report.pdf"],
        from_address="reports@myapp.com",
        from_name="MyApp Reports",
    )
"""

import mimetypes
import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Iterable, List, Optional, Union

from core.services.EnvService import EnvService

Recipients = Union[str, Iterable[str]]

DEFAULT_PORTS = {"ssl": 465, "tls": 587, "none": 25}


class MailService:
    def __init__(self, env_path: Optional[str] = None, env: Optional[EnvService] = None):
        """
        env_path: optional explicit path to a .env file, used only if `env` isn't given.
                  If omitted (and no `env`), MailService walks up from core/services/ to
                  the project root and loads .env there.
        env:      an existing EnvService to read config from, so callers that already have
                  one (e.g. AuthController via self.env()) don't reload/reparse .env again.
        """
        env = env or EnvService(env_path=env_path)

        self.driver = env.get("MAIL_DRIVER", "smtp").strip().lower()
        if self.driver not in ("smtp", "log"):
            raise ValueError(f"Unsupported MAIL_DRIVER '{self.driver}'. Expected 'smtp' or 'log'.")

        self.host = env.get("MAIL_HOST")
        self.encryption = env.get("MAIL_ENCRYPTION", "tls").strip().lower()  # tls | ssl | none
        self.port = env.get_int("MAIL_PORT", DEFAULT_PORTS.get(self.encryption, 587))
        self.username = env.get("MAIL_USERNAME")
        self.password = env.get("MAIL_PASSWORD")

        self.from_address = env.get("MAIL_FROM_ADDRESS")
        self.from_name = env.get("MAIL_FROM_NAME")

        log_path = env.get("MAIL_LOG_PATH")
        self.log_path = Path(log_path) if log_path else Path(self._project_root()) / "storage" / "logs" / "mail.log"

    # ------------------------------------------------------------------ #
    # setup
    # ------------------------------------------------------------------ #
    def _project_root(self) -> str:
        # this file lives at project/core/services/MailService.py
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    def send(
        self,
        to: Recipients,
        subject: str,
        body: str,
        html: Optional[str] = None,
        cc: Optional[Recipients] = None,
        bcc: Optional[Recipients] = None,
        attachments: Optional[List[str]] = None,
        from_address: Optional[str] = None,
        from_name: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> bool:
        """
        Sends (or, on the 'log' driver, logs) one email.
        to/cc/bcc each accept a single address string or a list of addresses.
        Returns True on success; raises on failure (bad config, SMTP error, missing attachment).
        """
        to_list = self._normalize_recipients(to)
        cc_list = self._normalize_recipients(cc)
        bcc_list = self._normalize_recipients(bcc)

        if not to_list:
            raise ValueError("MailService.send: at least one 'to' recipient is required")

        message = self._build_message(
            to_list=to_list,
            cc_list=cc_list,
            subject=subject,
            body=body,
            html=html,
            attachments=attachments,
            from_address=from_address,
            from_name=from_name,
            reply_to=reply_to,
        )

        # bcc is deliberately never added as a header - it's an extra envelope
        # recipient only, passed straight to the transport below
        all_recipients = to_list + cc_list + bcc_list

        if self.driver == "log":
            return self._write_log(message, all_recipients)
        return self._send_smtp(message, all_recipients)

    # ------------------------------------------------------------------ #
    # message building
    # ------------------------------------------------------------------ #
    @staticmethod
    def _normalize_recipients(recipients: Optional[Recipients]) -> List[str]:
        if not recipients:
            return []
        if isinstance(recipients, str):
            return [recipients]
        return list(recipients)

    def _build_message(
        self,
        to_list: List[str],
        cc_list: List[str],
        subject: str,
        body: str,
        html: Optional[str],
        attachments: Optional[List[str]],
        from_address: Optional[str],
        from_name: Optional[str],
        reply_to: Optional[str],
    ) -> EmailMessage:
        sender_address = from_address or self.from_address
        sender_name = from_name or self.from_name
        if not sender_address:
            raise ValueError(
                "MailService: no from address - set MAIL_FROM_ADDRESS in .env or pass from_address=..."
            )

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = formataddr((sender_name, sender_address)) if sender_name else sender_address
        message["To"] = ", ".join(to_list)
        if cc_list:
            message["Cc"] = ", ".join(cc_list)
        if reply_to:
            message["Reply-To"] = reply_to

        message.set_content(body or "")
        if html:
            message.add_alternative(html, subtype="html")

        for path in attachments or []:
            self._attach_file(message, path)

        return message

    @staticmethod
    def _attach_file(message: EmailMessage, path: str) -> None:
        file_path = Path(path)
        if not file_path.is_file():
            raise FileNotFoundError(f"MailService: attachment not found: {path}")
        mime_type, _ = mimetypes.guess_type(str(file_path))
        maintype, subtype = (mime_type or "application/octet-stream").split("/", 1)
        with open(file_path, "rb") as f:
            message.add_attachment(f.read(), maintype=maintype, subtype=subtype, filename=file_path.name)

    # ------------------------------------------------------------------ #
    # smtp driver
    # ------------------------------------------------------------------ #
    def _send_smtp(self, message: EmailMessage, recipients: List[str]) -> bool:
        if not self.host:
            raise ValueError("MailService: MAIL_HOST is not set")

        smtp_class = smtplib.SMTP_SSL if self.encryption == "ssl" else smtplib.SMTP
        with smtp_class(self.host, self.port, timeout=30) as server:
            if self.encryption == "tls":
                server.starttls()
            if self.username and self.password:
                server.login(self.username, self.password)
            server.send_message(message, to_addrs=recipients)
        return True

    # ------------------------------------------------------------------ #
    # log driver - for local dev, writes instead of sending
    # ------------------------------------------------------------------ #
    def _write_log(self, message: EmailMessage, recipients: List[str]) -> bool:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write("=" * 70 + "\n")
            f.write(f"Envelope recipients: {', '.join(recipients)}\n")
            f.write(str(message))
            f.write("\n\n")
        return True
