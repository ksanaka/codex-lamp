"""Classification of Codex replies that request user input."""

from __future__ import annotations


def requests_input(message: str | None, markers: tuple[str, ...]) -> bool:
    """Return whether a reply ends in a question or contains an input marker."""
    if not message or not (normalized := message.strip()):
        return False

    normalized = normalized.casefold()
    return normalized.endswith(("?", "？")) or any(
        (normalized_marker := marker.strip().casefold()) and normalized_marker in normalized
        for marker in markers
    )
