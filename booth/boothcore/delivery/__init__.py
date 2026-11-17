"""Ways a guest gets the result out of the booth: e-mail today, more later."""
from .mail import MailSender, valid_address, normalise_address, MAX_ATTACHMENT_BYTES, MAX_ATTEMPTS

__all__ = ["MailSender", "valid_address", "normalise_address", "MAX_ATTACHMENT_BYTES", "MAX_ATTEMPTS"]
