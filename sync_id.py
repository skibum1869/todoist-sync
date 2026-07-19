import re
import uuid

TAG_PREFIX = "sync_id:"
_PATTERN = re.compile(r"sync_id:([0-9a-fA-F-]{36})")


def new_id() -> str:
    return str(uuid.uuid4())


def extract(text: str | None) -> str | None:
    if not text:
        return None
    match = _PATTERN.search(text)
    return match.group(1) if match else None


def append_tag(text: str | None, sid: str) -> str:
    text = (text or "").rstrip()
    tag = f"{TAG_PREFIX}{sid}"
    return f"{text}\n\n{tag}" if text else tag
