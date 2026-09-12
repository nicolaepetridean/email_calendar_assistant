from pathlib import Path

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def render(name: str, **kwargs) -> str:
    """Load a template by name and interpolate kwargs into {placeholders}."""
    return (_TEMPLATE_DIR / f"{name}.txt").read_text().format_map(kwargs)
