"""Importing this package registers every platform's send() function
with the registry, so core.commands can deliver a message to any
platform without knowing which adapter is currently handling a request.

Add a new platform by creating core/senders/<platform>.py that calls
register(...), then importing it here.
"""
from core.senders import whatsapp  # noqa: F401
from core.senders import telegram  # noqa: F401
from core.senders import gowa  # noqa: F401
from core.senders.registry import send  # noqa: F401
