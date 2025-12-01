from edge_compat import json
from edge_config import RETURN_TEXT_KEEP, RETURN_TEXT_LIMIT


def _truncate_text(value):
    if isinstance(value, str) and len(value) > RETURN_TEXT_LIMIT:
        head = value[:RETURN_TEXT_KEEP]
        tail = value[-RETURN_TEXT_KEEP:]
        return "{}...[truncated]...{}".format(head, tail)
    return value


def _format_for_log(value, max_length=400):
    """Convert arbitrary value to a short printable string."""
    # MicroPython 環境でも扱いやすいようログ出力文字列を整形
    try:
        text = json.dumps(value)
    except Exception:
        try:
            text = str(value)
        except Exception:
            text = "<unprintable>"

    if text and len(text) > max_length:
        return text[: max_length - 16] + "...<truncated>"
    return text


__all__ = ["_truncate_text", "_format_for_log"]
