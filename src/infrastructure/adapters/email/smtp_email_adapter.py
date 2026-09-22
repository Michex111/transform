"""SMTP email transport.

Provider-agnostic: works with any SMTP relay (SendGrid, Postmark, Mailgun,
Amazon SES, Google Workspace, ...) so an existing company mail setup needs no
new vendor.

Two details that matter in practice:

* ``smtplib`` is fully blocking, so every call runs in a worker thread via
  ``asyncio.to_thread``. A synchronous send inside an async handler would stall
  the entire event loop for the duration of the SMTP conversation (connect +
  STARTTLS + AUTH + DATA can be several seconds).
* The message is built as ``multipart/alternative`` with the plain-text part
  first. Ordering is not cosmetic: clients render the *last* part they
  understand, so HTML must come after text, and providing both is what keeps
  the mail out of spam filters.
"""

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage as MimeMessage
from email.utils import formataddr, make_msgid

from src.application.dtos.email_dto import EmailMessage


class SMTPEmailAdapter:
    """Sends mail through an SMTP relay."""

    def __init__(
        self,
        *,
        host: str,
        port: int = 587,
        username: str | None = None,
        password: str | None = None,
        from_address: str,
        from_name: str = "",
        reply_to: str | None = None,
        use_starttls: bool = True,
        use_ssl: bool = False,
        timeout_seconds: int = 15,
        logger: logging.Logger | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._from_address = from_address
        self._from_name = from_name
        self._reply_to = reply_to
        self._use_starttls = use_starttls
        self._use_ssl = use_ssl
        self._timeout = timeout_seconds
        self._logger = logger or logging.getLogger("file_converter_api.email")

    def _build_mime(self, message: EmailMessage) -> MimeMessage:
        mime = MimeMessage()
        mime["From"] = formataddr((self._from_name or "", self._from_address))
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime["Message-ID"] = make_msgid(domain=self._from_address.partition("@")[2] or None)
        reply_to = message.reply_to or self._reply_to
        if reply_to:
            mime["Reply-To"] = reply_to
        # text first, html second — see the module docstring.
        mime.set_content(message.text_body)
        mime.add_alternative(message.html_body, subtype="html")
        return mime

    def _send_sync(self, message: EmailMessage) -> None:
        mime = self._build_mime(message)
        context = ssl.create_default_context()

        if self._use_ssl:
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                self._host, self._port, timeout=self._timeout, context=context
            )
        else:
            server = smtplib.SMTP(self._host, self._port, timeout=self._timeout)

        try:
            if self._use_starttls and not self._use_ssl:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
            if self._username and self._password:
                server.login(self._username, self._password)
            server.send_message(mime)
        finally:
            # Best-effort QUIT: an exception while closing must not mask the
            # original send failure (or turn a successful send into a failure).
            try:
                server.quit()
            except Exception:  # noqa: BLE001 - close is best-effort
                pass

    async def send(self, message: EmailMessage) -> None:
        await asyncio.to_thread(self._send_sync, message)
        self._logger.info("Verification email accepted by %s for %s", self._host, message.to)
