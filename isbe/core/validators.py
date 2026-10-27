"""Pure checks with no I/O, shared by settings and the feature modules.

Provider defaults, the per-key setting rules, source URL validation and the
mail setting and recipient checks. Messages are shown to the user verbatim and
never contain the checked value.

Two URL grammars are in play: source and search URLs are parsed and
re-serialized by the WHATWG parser (ada-url), while service base URLs are only
checked against the URI grammar implemented below and kept as typed.
"""

import ipaddress
from dataclasses import dataclass

import ada_url

from isbe.core import jsonfmt
from isbe.core.errors import HttpError

# Unicode White_Space, the set `trim` removes.
_WHITE = frozenset(
    "\t\n\x0b\x0c\r \x85\xa0        "
    "        　"
)
PROVIDERS = ("anthropic", "deepseek", "openai")
# Fields every request body sets itself; ISBE_LLM_EXTRA_BODY must not replace them.
RESERVED_BODY = frozenset(
    {"model", "messages", "system", "stream", "max_tokens", "max_completion_tokens"}
)


class Invalid(HttpError):
    """A value the user must correct; the message is safe to show."""

    def __init__(self, message: str) -> None:
        super().__init__(400, message)


def is_space(c: str) -> bool:
    return c in _WHITE


def is_control(c: str) -> bool:
    """Unicode general category Cc."""
    n = ord(c)
    return n < 0x20 or 0x7F <= n <= 0x9F


def trim(value: str) -> str:
    """Strip Unicode White_Space (which differs from `str.strip`)."""
    start, end = 0, len(value)
    while start < end and value[start] in _WHITE:
        start += 1
    while end > start and value[end - 1] in _WHITE:
        end -= 1
    return value[start:end]


def ascii_lower(value: str) -> str:
    """Only A-Z change (`str.lower` also folds e.g. the Kelvin sign)."""
    return value.translate(_ASCII_LOWER)


_ASCII_LOWER = {c: c + 32 for c in range(ord("A"), ord("Z") + 1)}


def byte_len(value: str) -> int:
    """Length in UTF-8 bytes."""
    return len(value.encode("utf-8", "surrogatepass"))


def parse_uint(value: str, maximum: int) -> int | None:
    """Unsigned integer (optional `+`, ASCII digits only) within 0..=maximum."""
    digits = value[1:] if value.startswith("+") else value
    if not digits or not digits.isascii() or not digits.isdigit():
        return None
    number = int(digits)
    return number if number <= maximum else None


def parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Strict IP address parse: no zone ids, prefixes, brackets or IPv4 shorthand."""
    if not value or "%" in value or "/" in value or not value.isascii():
        return None
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def provider_defaults(name: str) -> tuple[str, str, str]:
    """Base URL, fast model and smart model, also shown by the settings console."""
    match name:
        case "anthropic":
            return ("https://api.anthropic.com", "claude-haiku-5-5", "claude-sonnet-5-5")
        case "deepseek":
            return ("https://api.deepseek.com", "deepseek-chat", "deepseek-chat")
        case "openai":
            return ("https://api.openai.com/v1", "gpt-5-mini", "gpt-5")
    raise Invalid("ISBE_LLM_PROVIDER must be anthropic, deepseek or openai")


# --- URI grammar for service base URLs -------------------------------------------------

_AUTHORITY_CHARS = frozenset(
    b"!#$&'()*+,-./0123456789:;=?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[]_abcdefghijklmnopqrstuvwxyz~"
)
_SCHEME_CHARS = frozenset(b"+-.0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
_PATH_BYTES = frozenset(
    {0x21, *range(0x24, 0x3C), 0x3D, *range(0x40, 0x60), *range(0x61, 0x7B), 0x7C, 0x7E}
    | set(b'"{}')
    | set(range(0x7F, 0x100))
)
_QUERY_BYTES = frozenset({0x21, *range(0x24, 0x3C), 0x3D, *range(0x3F, 0x7F), *range(0x7F, 0x100)})


@dataclass(frozen=True)
class HttpUri:
    scheme: str
    authority: str
    host: str
    query: str | None


def _authority_end(data: bytes) -> int | None:
    colons, start_bracket, end_bracket, percent, at = 0, False, False, False, None
    end = len(data)
    for i, b in enumerate(data):
        if b in b"/?#":
            end = i
            break
        if b == ord(":"):
            if colons >= 8:
                return None
            colons += 1
        elif b == ord("["):
            if percent or start_bracket:
                return None
            start_bracket = True
        elif b == ord("]"):
            if not start_bracket or end_bracket:
                return None
            end_bracket, colons, percent = True, 0, False
        elif b == ord("@"):
            at, colons, percent = i, 0, False
        elif b == ord("%"):
            percent = True
        elif b not in _AUTHORITY_CHARS:
            return None
    if start_bracket != end_bracket or colons > 1 or percent:
        return None
    if end > 0 and at == end - 1:
        return None
    return end


def http_uri(value: str) -> HttpUri | None:
    """An absolute URI as `value.parse::<http::Uri>()` accepts it, or None."""
    data = value.encode("utf-8", "surrogatepass")
    if not data or len(data) > 0xFFFE or data[0] == ord("/"):
        return None
    if data[:7].lower() == b"http://":
        scheme, rest = "http", data[7:]
    elif data[:8].lower() == b"https://":
        scheme, rest = "https", data[8:]
    else:
        scheme, rest = "", b""
        if len(data) > 3:
            for i, b in enumerate(data):
                if b == ord(":"):
                    if len(data) >= i + 3 and data[i + 1 : i + 3] == b"//" and i <= 64:
                        scheme, rest = data[:i].decode(), data[i + 3 :]
                    break
                if b not in _SCHEME_CHARS:
                    break
        if not scheme:
            return None
    end = _authority_end(rest)
    if not end:
        return None
    authority, tail = rest[:end].decode(), rest[end:]
    path_end = len(tail)
    for i, b in enumerate(tail):
        if b in b"?#":
            path_end = i
            break
        if b not in _PATH_BYTES:
            return None
    query = None
    if tail[path_end : path_end + 1] == b"?":
        raw = tail[path_end + 1 :].split(b"#", 1)[0]
        if any(b not in _QUERY_BYTES for b in raw):
            return None
        query = raw.decode("utf-8", "surrogatepass")
    host_port = authority.rsplit("@", 1)[-1]
    if host_port.startswith("["):
        host = host_port[: host_port.find("]") + 1]
    else:
        host = host_port.split(":", 1)[0]
    return HttpUri(scheme, authority, host, query)


def _service_url(name: str, value: str) -> None:
    uri = http_uri(value)
    if not (
        uri is not None
        and uri.scheme in ("http", "https")
        and uri.host
        and "@" not in uri.authority
        and "#" not in value
    ):
        raise Invalid(f"{name} must be an http(s) URL without credentials or a fragment")


# --- WHATWG URLs ---------------------------------------------------------------------


def whatwg(value: str, base: str | None = None) -> ada_url.URL:
    """WHATWG parse; raises ValueError."""
    return ada_url.URL(value) if base is None else ada_url.URL(value, base)


def has_query(url: ada_url.URL) -> bool:
    return "?" in url.href.split("#", 1)[0]


def has_fragment(url: ada_url.URL) -> bool:
    return "#" in url.href


def validate_url(value: str) -> str:
    """A source URL in canonical WHATWG form."""
    if (
        not value
        or byte_len(value) > 2048
        or any(is_space(c) or is_control(c) for c in value)
        or "\\" in value
    ):
        raise Invalid("source URL must be at most 2048 bytes without whitespace or backslashes")
    try:
        url = whatwg(value)
    except ValueError:
        raise Invalid("invalid source URL") from None
    authority = None
    if "://" in value:
        rest = value.split("://", 1)[1]
        for stop in "/?#":
            rest = rest.split(stop, 1)[0]
        authority = rest
    if (
        url.protocol not in ("http:", "https:")
        or not url.hostname
        or url.username
        or url.password
        or has_fragment(url)
        or authority is None
        or "@" in authority
    ):
        raise Invalid(
            "source URL must use HTTP(S), with a host and without credentials or fragments"
        )
    return url.href


# --- mail ----------------------------------------------------------------------------

_LOCAL_SPECIALS = frozenset(".!#$%&'*+-/=?^_`{|}~")


def _alnum(c: str) -> bool:
    return c.isascii() and c.isalnum()


def _label(part: str) -> bool:
    return (
        bool(part)
        and len(part) <= 63
        and not part.startswith("-")
        and not part.endswith("-")
        and all(_alnum(c) or c == "-" for c in part)
    )


def address(value: str) -> bool:
    """A bare ASCII mailbox `local@domain.tld`."""
    if "@" not in value:
        return False
    local, domain = value.split("@", 1)
    return (
        byte_len(value) <= 254
        and bool(local)
        and len(local) <= 64
        and not local.startswith(".")
        and not local.endswith(".")
        and ".." not in local
        and all(_alnum(c) or c in _LOCAL_SPECIALS for c in local)
        and "." in domain
        and all(_label(part) for part in domain.split("."))
    )


def recipients(value: str, allow_empty: bool) -> list[str]:
    """Addresses split on commas, semicolons or whitespace, deduplicated ignoring case."""
    if byte_len(value) > 4096 or any(is_control(c) and c not in "\n\r\t" for c in value):
        raise Invalid("Recipients are too long or contain control characters")
    items, current = [], []
    for c in value:
        if c in ",;" or is_space(c):
            items.append("".join(current))
            current = []
        else:
            current.append(c)
    items.append("".join(current))
    seen: set[str] = set()
    result: list[str] = []
    for item in filter(None, items):
        if not address(item):
            raise Invalid("Enter valid email addresses separated by commas, semicolons or spaces")
        key = ascii_lower(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    if len(result) > 20:
        raise Invalid("At most 20 recipients are allowed per email")
    if not allow_empty and not result:
        raise Invalid("At least one recipient is required")
    return result


def validate_mail_setting(name: str, value: str) -> None:
    """Check one mail setting: partial edits as well as the active transport."""
    if byte_len(value) > 4096 or any(is_control(c) for c in value):
        raise Invalid("Email setting is too long or contains control characters")
    match name:
        case "EMAIL_TRANSPORT" if value not in ("smtp", "zeptomail_api"):
            raise Invalid("EMAIL_TRANSPORT must be smtp or zeptomail_api")
        case "SMTP_HOST":
            hostname = bool(value) and byte_len(value) <= 253 and all(map(_label, value.split(".")))
            if not hostname and parse_ip(value) is None:
                raise Invalid(
                    "Configure SMTP_HOST with a hostname or IP address, without a scheme or port"
                )
        case "SMTP_PORT" if not parse_uint(value, 65535):
            raise Invalid("SMTP_PORT must be from 1 to 65535")
        case "SMTP_SECURITY" if value not in ("tls", "starttls"):
            raise Invalid("SMTP_SECURITY must be tls or starttls")
        case "SMTP_FROM" if not address(value):
            raise Invalid("Configure a valid SMTP_FROM address")
        case "SMTP_FROM_NAME" if byte_len(value) > 200:
            raise Invalid("SMTP_FROM_NAME must be at most 200 bytes")
        case "ZEPTOMAIL_API_URL":
            uri = http_uri(value)
            if not (
                uri is not None
                and uri.scheme == "https"
                and uri.host
                and "@" not in uri.authority
                and uri.query is None
                and "#" not in value
            ):
                raise Invalid(
                    "Configure ZEPTOMAIL_API_URL with the verified HTTPS endpoint for your account"
                )
        case "ZEPTOMAIL_TOKEN" if not value or ascii_lower(value) == "zoho-enczapikey":
            raise Invalid("Configure a valid ZEPTOMAIL_TOKEN")


# --- settings ------------------------------------------------------------------------

_MAIL = frozenset(
    {
        "EMAIL_TRANSPORT",
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_SECURITY",
        "SMTP_USER",
        "SMTP_PASSWORD",
        "ZEPTOMAIL_API_URL",
        "ZEPTOMAIL_TOKEN",
        "SMTP_FROM",
        "SMTP_FROM_NAME",
    }
)
_SERVICE_URLS = frozenset(
    {"ISBE_LLM_BASE_URL", "ESSAIES_URL", "OPENALEX_API_URL", "METRAIL_API_URL", "ARXIV_API_URL", "GITHUB_API_URL"}
)


def extra_body(value: str) -> str:
    """ISBE_LLM_EXTRA_BODY as compact JSON; it may not replace the fields ISBE sets."""
    try:
        body = jsonfmt.loads(value)
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise Invalid("ISBE_LLM_EXTRA_BODY must be a JSON object")
    reserved = sorted(RESERVED_BODY & body.keys())
    if reserved:
        raise Invalid(
            f"ISBE_LLM_EXTRA_BODY must not set {', '.join(reserved)}: ISBE sets the model, "
            "messages, system prompt, streaming and output token limit itself"
        )
    return jsonfmt.dumps(body)


def setting(name: str, value: str | None) -> str | None:
    """Normalize one known setting; None or blank clears it.

    The caller has already rejected names outside `settings.KEYS`.
    """
    if value is None:
        return None
    limit = 16 * 1024 if name == "ISBE_LLM_EXTRA_BODY" else 4096
    if byte_len(value) > limit:
        raise Invalid(f"{name} exceeds {limit} bytes")
    if any(
        is_control(c) and not (name == "ISBE_LLM_EXTRA_BODY" and c in "\n\r\t") for c in value
    ):
        raise Invalid(f"{name} contains control characters")
    value = trim(value)
    if not value:
        return None
    match name:
        case _ if name in _MAIL:
            validate_mail_setting(name, value)
        case "ISBE_EMAIL_DEFAULT_RECIPIENTS":
            value = ", ".join(recipients(value, True))
        case "ISBE_LLM_PROVIDER":
            provider_defaults(value)
        case "ISBE_LLM_NO_AUTH" if value not in ("true", "false"):
            raise Invalid("ISBE_LLM_NO_AUTH must be true or false")
        case "ISBE_FULLTEXT_EXTRACTOR" if value not in ("llm", "metrail", "off"):
            raise Invalid("ISBE_FULLTEXT_EXTRACTOR must be llm, metrail or off")
        case "ISBE_LLM_MAX_TOKENS" | "ISBE_LLM_TIMEOUT_S":
            limit = 1_000_000 if name == "ISBE_LLM_MAX_TOKENS" else 86_400
            if not parse_uint(value, limit):
                raise Invalid(f"{name} must be an integer from 1 to {limit}")
        case "ISBE_ARTICLE_DNS" if value not in ("system", "cloudflare"):
            raise Invalid("ISBE_ARTICLE_DNS must be system or cloudflare")
        case "ISBE_PAPER_SOURCE" if value not in ("openalex", "essaies"):
            raise Invalid("ISBE_PAPER_SOURCE must be openalex or essaies")
        case "OPENALEX_MAILTO" if not address(value):
            raise Invalid("OPENALEX_MAILTO must be one email address")
        case "OPENALEX_API_KEY" if not all(c.isascii() and c.isprintable() and not c.isspace() for c in value):
            raise Invalid("OPENALEX_API_KEY must be printable ASCII without spaces")
        case "SEARXNG_URL":
            try:
                value = validate_url(value)
            except Invalid:
                raise Invalid(
                    "SEARXNG_URL must be an HTTP(S) base URL without credentials or a fragment"
                ) from None
            if has_query(whatwg(value)):
                raise Invalid("SEARXNG_URL must be a base URL without query parameters")
        case "ISBE_LLM_EXTRA_BODY":
            value = extra_body(value)
        case _ if name in _SERVICE_URLS:
            _service_url(name, value)
    return value
