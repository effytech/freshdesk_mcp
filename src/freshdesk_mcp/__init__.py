# Defined before importing .server, which reads __version__ to report the
# server version during MCP initialization.
__version__ = "1.3.0"

from .server import main  # noqa: E402

__all__ = ["main"]
