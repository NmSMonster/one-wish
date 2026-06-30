"""GUI API: serwer WebSocket emitujący kontrakt do GUI (read-only cockpit)."""
from .gui_ws import GuiApiServer, event_to_gui

__all__ = ["GuiApiServer", "event_to_gui"]
