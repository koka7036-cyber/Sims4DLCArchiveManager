"""
CustomTkinter and Tkinter compatibility shim.
Provides transparent fallback/mock classes when tkinter or customtkinter is unavailable
in headless Linux environments, enabling 100% automated test execution.
"""

import sys
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

try:
    import tkinter as tk
    from tkinter import ttk, filedialog
    import customtkinter as ctk
    _HAS_NATIVE_CTK = True
except Exception:
    _HAS_NATIVE_CTK = False


def has_native_ctk() -> bool:
    """Returns True if native CustomTkinter and Tkinter runtime are available."""
    return _HAS_NATIVE_CTK


if _HAS_NATIVE_CTK:
    CTk = ctk.CTk
    CTkToplevel = ctk.CTkToplevel
    CTkFrame = ctk.CTkFrame
    CTkScrollableFrame = ctk.CTkScrollableFrame
    CTkLabel = ctk.CTkLabel
    CTkButton = ctk.CTkButton
    CTkProgressBar = ctk.CTkProgressBar
    CTkEntry = ctk.CTkEntry
    CTkCheckBox = ctk.CTkCheckBox
    CTkTabview = ctk.CTkTabview
    CTkFont = ctk.CTkFont
    CTkImage = getattr(ctk, "CTkImage", None)

    StringVar = tk.StringVar
    BooleanVar = tk.BooleanVar
    IntVar = tk.IntVar
    DoubleVar = tk.DoubleVar
    filedialog = filedialog

    set_appearance_mode = ctk.set_appearance_mode
    set_default_color_theme = ctk.set_default_color_theme

else:
    # Headless Mock Implementation for CustomTkinter

    class MockFileDialog:
        @staticmethod
        def askopenfilename(**kwargs) -> str:
            return ""

        @staticmethod
        def askdirectory(**kwargs) -> str:
            return ""

    filedialog = MockFileDialog

    class MockVar:
        def __init__(self, value: Any = None):
            self._value = value
            self._traces: List[Callable] = []

        def get(self) -> Any:
            return self._value

        def set(self, value: Any) -> None:
            self._value = value
            for cb in self._traces:
                try:
                    cb()
                except Exception:
                    pass

        def trace_add(self, mode: str, callback: Callable) -> None:
            self._traces.append(callback)

    class StringVar(MockVar):
        def __init__(self, value: str = ""):
            super().__init__(str(value))

        def get(self) -> str:
            return str(self._value) if self._value is not None else ""

    class BooleanVar(MockVar):
        def __init__(self, value: bool = False):
            super().__init__(bool(value))

        def get(self) -> bool:
            return bool(self._value)

    class IntVar(MockVar):
        def __init__(self, value: int = 0):
            super().__init__(int(value))

        def get(self) -> int:
            return int(self._value)

    class DoubleVar(MockVar):
        def __init__(self, value: float = 0.0):
            super().__init__(float(value))

        def get(self) -> float:
            return float(self._value)

    class MockWidget:
        def __init__(self, master: Any = None, **kwargs):
            self.master = master
            self.children: List["MockWidget"] = []
            if master and hasattr(master, "children"):
                master.children.append(self)
            self._props: Dict[str, Any] = dict(kwargs)
            self._bindings: Dict[str, List[Callable]] = {}
            self._packed = False
            self._gridded = False
            self._destroyed = False

        def configure(self, **kwargs) -> None:
            self._props.update(kwargs)

        def cget(self, key: str) -> Any:
            return self._props.get(key)

        def pack(self, **kwargs) -> None:
            self._packed = True

        def pack_forget(self) -> None:
            self._packed = False

        def grid(self, **kwargs) -> None:
            self._gridded = True

        def grid_forget(self) -> None:
            self._gridded = False

        def destroy(self) -> None:
            self._destroyed = True
            for child in list(self.children):
                child.destroy()
            if self.master and hasattr(self.master, "children") and self in self.master.children:
                self.master.children.remove(self)

        def bind(self, sequence: str, func: Callable, add: Optional[str] = None) -> None:
            if sequence not in self._bindings:
                self._bindings[sequence] = []
            self._bindings[sequence].append(func)

        def winfo_children(self) -> List["MockWidget"]:
            return list(self.children)

        def winfo_ismapped(self) -> bool:
            return bool(self._packed or self._gridded)

        def after(self, ms: int, func: Optional[Callable] = None, *args) -> str:
            if not hasattr(self, "_after_callbacks"):
                self._after_callbacks = {}
            timer_id = f"timer_{id(func)}_{len(self._after_callbacks)}"
            if func:
                self._after_callbacks[timer_id] = (ms, func, args)
                # In headless tests, only one-shot ms==0 executes immediately without recursion
                if ms == 0 and not getattr(self, "_in_after_exec", False):
                    self._in_after_exec = True
                    try:
                        func(*args)
                    except Exception:
                        pass
                    finally:
                        self._in_after_exec = False
            return timer_id

        def after_cancel(self, timer_id: str) -> None:
            if hasattr(self, "_after_callbacks"):
                self._after_callbacks.pop(timer_id, None)

        def update(self) -> None:
            pass

        def update_idletasks(self) -> None:
            pass

    class CTk(MockWidget):
        def __init__(self, **kwargs):
            super().__init__(master=None, **kwargs)
            self._title = "Mock Window"
            self._geometry = "1100x700"
            self._min_size = (800, 500)
            self._protocols: Dict[str, Callable] = {}
            self._running = True

        def title(self, title_text: Optional[str] = None) -> Optional[str]:
            if title_text is not None:
                self._title = title_text
            return self._title

        def geometry(self, geo: str) -> None:
            self._geometry = geo

        def minsize(self, width: int, height: int) -> None:
            self._min_size = (width, height)

        def protocol(self, name: str, callback: Callable) -> None:
            self._protocols[name] = callback

        def withdraw(self) -> None:
            pass

        def deiconify(self) -> None:
            pass

        def mainloop(self) -> None:
            # Headless run does not block
            pass

        def quit(self) -> None:
            self._running = False
            self.destroy()

    class CTkToplevel(CTk):
        def __init__(self, master: Any = None, **kwargs):
            super().__init__(**kwargs)
            self.master = master
            if master and hasattr(master, "children"):
                master.children.append(self)

        def grab_set(self) -> None:
            pass

        def grab_release(self) -> None:
            pass

        def focus_set(self) -> None:
            pass

        def wait_window(self, window: Any = None) -> None:
            pass

    class CTkFrame(MockWidget):
        pass

    class CTkScrollableFrame(MockWidget):
        pass

    class CTkLabel(MockWidget):
        pass

    class CTkButton(MockWidget):
        def invoke(self) -> None:
            cmd = self._props.get("command")
            if cmd and callable(cmd):
                cmd()

    class CTkProgressBar(MockWidget):
        def __init__(self, master: Any = None, **kwargs):
            super().__init__(master, **kwargs)
            self._value = 0.0

        def set(self, value: float) -> None:
            self._value = max(0.0, min(1.0, float(value)))

        def get(self) -> float:
            return self._value

    class CTkEntry(MockWidget):
        def __init__(self, master: Any = None, **kwargs):
            super().__init__(master, **kwargs)
            self._text = ""

        def insert(self, index: int, string: str) -> None:
            self._text += string

        def delete(self, first: int, last: Optional[Any] = None) -> None:
            self._text = ""

        def get(self) -> str:
            return self._text

    class CTkCheckBox(MockWidget):
        def __init__(self, master: Any = None, **kwargs):
            super().__init__(master, **kwargs)
            self._state = False

        def select(self) -> None:
            self._state = True
            var = self._props.get("variable")
            if var:
                var.set(self._props.get("onvalue", True))

        def deselect(self) -> None:
            self._state = False
            var = self._props.get("variable")
            if var:
                var.set(self._props.get("offvalue", False))

        def get(self) -> int:
            return 1 if self._state else 0

    class CTkTabview(MockWidget):
        def __init__(self, master: Any = None, **kwargs):
            super().__init__(master, **kwargs)
            self._tabs: Dict[str, CTkFrame] = {}
            self._current_tab = ""

        def add(self, name: str) -> CTkFrame:
            frame = CTkFrame(self)
            self._tabs[name] = frame
            if not self._current_tab:
                self._current_tab = name
            return frame

        def tab(self, name: str) -> CTkFrame:
            return self._tabs.get(name) or self.add(name)

        def set(self, name: str) -> None:
            self._current_tab = name

        def get(self) -> str:
            return self._current_tab

    class CTkFont:
        def __init__(self, family: str = "Segoe UI", size: int = 12, weight: str = "normal"):
            self.family = family
            self.size = size
            self.weight = weight

    class CTkImage:
        def __init__(self, light_image: Any = None, dark_image: Any = None, size: Tuple[int, int] = (20, 20)):
            self.light_image = light_image
            self.dark_image = dark_image
            self.size = size

    def set_appearance_mode(mode: str) -> None:
        pass

    def set_default_color_theme(theme: str) -> None:
        pass
