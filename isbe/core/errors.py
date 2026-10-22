"""Errors that cross layer boundaries.

Feature code raises `HttpError` for a failure the user can act on; its message
is shown verbatim, so it must never contain secrets or provider responses. Any
other exception is internal: the web layer logs it and answers 500 with a
generic message, and the CLI prints it.
"""


class HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def describe(error: BaseException) -> str:
    """The message and its explicit causes joined by ": ".

    Context is added by raising it `from` the cause (`"triage call to m: HTTP 429: ..."`).
    Implicit `__context__` is not a cause and is left out, as is a repeated message.
    """
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = str(current) or type(current).__name__
        if not parts or parts[-1] != text:
            parts.append(text)
        current = current.__cause__
    return ": ".join(parts)
