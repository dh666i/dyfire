"""Pure shared error types; HTTP services do not import browser executors."""


class TargetNotFoundError(RuntimeError):
    """The requested friend is absent from the synchronized web chat list."""


class WebChatLoginRequiredError(RuntimeError):
    """The web chat page requires the account to sign in again."""
