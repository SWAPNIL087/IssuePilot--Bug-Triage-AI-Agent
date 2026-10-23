import re


def split_front_matter(raw: str) -> tuple[dict, str]:
    """Split optional `key: value` front matter (between --- lines) from the text."""
    raw = raw.replace("\r\n", "\n")
    m = re.match(r"---[ \t]*\n(.*?)\n---[ \t]*(?:\n|$)", raw, re.S)
    if not m:
        return {}, raw
    meta = {}
    for line in m.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    return meta, raw[m.end():]
