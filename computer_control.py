"""Bezpečné a auditované lokální ovládání Windows pro Raven.

Modul používá relativně malé vestavěné Win32 API a Pillow. Volitelně využije
pywinauto pro UI Automation strom, pokud je balíček v portable Pythonu dostupný.
Veškerý vstup je cílený na konkrétní okno a sekvence se provádějí sériově.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


IS_WINDOWS = os.name == "nt"


class ComputerControlError(ValueError):
    """Očekávaná chyba validace nebo bezpečnostní pojistky."""


if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ULONG_PTR = wintypes.WPARAM

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                    ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                    ("wParamH", wintypes.WORD)]

    class INPUT_UNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("value",)
        _fields_ = [("type", wintypes.DWORD), ("value", INPUT_UNION)]

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    EnumChildProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT


INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000
SW_RESTORE = 9
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

VK: dict[str, int] = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "shift": 0x10,
    "ctrl": 0x11, "control": 0x11, "alt": 0x12, "escape": 0x1B,
    "esc": 0x1B, "space": 0x20, "pageup": 0x21, "pagedown": 0x22,
    "end": 0x23, "home": 0x24, "left": 0x25, "up": 0x26,
    "right": 0x27, "down": 0x28, "insert": 0x2D, "delete": 0x2E,
    "win": 0x5B, "windows": 0x5B,
}
VK.update({f"f{i}": 0x6F + i for i in range(1, 13)})
VK.update({chr(code).lower(): code for code in range(ord("A"), ord("Z") + 1)})
VK.update({str(i): ord(str(i)) for i in range(10)})

PROTECTED_TOKENS = {
    "credential", "windows security", "zabezpečení windows", "winlogon",
    "password", "heslo", "keepass", "bitwarden", "1password", "lastpass",
    "credentialui", "consent.exe", "lockapp.exe",
}


def _window_text(hwnd: int) -> str:
    if not IS_WINDOWS:
        return ""
    length = max(0, user32.GetWindowTextLengthW(hwnd))
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value.strip()


def _class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, len(buffer))
    return buffer.value.strip()


def _process_path(pid: int) -> str:
    if not IS_WINDOWS or pid <= 0:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _rect(hwnd: int) -> dict[str, int]:
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return {"left": 0, "top": 0, "right": 0, "bottom": 0, "width": 0, "height": 0}
    return {
        "left": rect.left, "top": rect.top, "right": rect.right, "bottom": rect.bottom,
        "width": max(0, rect.right - rect.left), "height": max(0, rect.bottom - rect.top),
    }


class ComputerController:
    """Serializovaný vykonavatel Win32 vstupů se snímky a auditem."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.runtime_dir = self.root / "runtime" / "computer-control"
        self.screenshot_dir = self.runtime_dir / "screenshots"
        self.audit_path = self.runtime_dir / "audit.jsonl"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._active_run = ""
        self._last_result: dict[str, Any] = {}
        # Všechny COM/UI Automation objekty žijí v jediném stálém vlákně.
        # comtypes není bezpečné vytvářet v krátkodobých HTTP/async vláknech.
        self._uia_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="raven-uia")

    @staticmethod
    def _require_windows() -> None:
        if not IS_WINDOWS:
            raise ComputerControlError("Ovládání počítače je dostupné pouze ve Windows.")

    def screen_bounds(self) -> dict[str, int]:
        self._require_windows()
        left = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
        top = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
        width = int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
        height = int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
        return {"left": left, "top": top, "width": width, "height": height,
                "right": left + width, "bottom": top + height}

    def windows(self) -> list[dict[str, Any]]:
        self._require_windows()
        result: list[dict[str, Any]] = []
        foreground = int(user32.GetForegroundWindow() or 0)

        @EnumWindowsProc
        def callback(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            title = _window_text(hwnd)
            bounds = _rect(hwnd)
            if not title or bounds["width"] < 2 or bounds["height"] < 2:
                return True
            pid_value = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_value))
            process_path = _process_path(int(pid_value.value))
            process = Path(process_path).name if process_path else ""
            result.append({
                "hwnd": int(hwnd), "title": title[:500], "class_name": _class_name(hwnd),
                "pid": int(pid_value.value), "process": process, "process_path": process_path,
                "bounds": bounds, "foreground": int(hwnd) == foreground,
                "protected": self._is_protected(title, process, _class_name(hwnd)),
            })
            return True

        if not user32.EnumWindows(callback, 0):
            raise ComputerControlError("Windows nedokázal vypsat otevřená okna.")
        return sorted(result, key=lambda item: (not item["foreground"], item["title"].lower()))

    @staticmethod
    def _is_protected(title: str, process: str, class_name: str = "") -> bool:
        haystack = f"{title} {process} {class_name}".lower()
        return any(token in haystack for token in PROTECTED_TOKENS)

    def _window(self, hwnd_value: Any) -> dict[str, Any]:
        try:
            hwnd = int(hwnd_value)
        except (TypeError, ValueError) as error:
            raise ComputerControlError("Akce musí obsahovat platné ID cílového okna.") from error
        match = next((item for item in self.windows() if item["hwnd"] == hwnd), None)
        if not match:
            raise ComputerControlError("Cílové okno už neexistuje nebo není viditelné.")
        if match["protected"]:
            raise ComputerControlError("Raven z bezpečnostních důvodů neovládá přihlašovací nebo heslové okno.")
        return match

    def _check_stop(self) -> None:
        if self._stop.is_set():
            raise ComputerControlError("Ovládání počítače bylo nouzově zastaveno.")
        point = POINT()
        if user32.GetCursorPos(ctypes.byref(point)) and point.x <= 1 and point.y <= 1:
            self._stop.set()
            raise ComputerControlError("Nouzová pojistka: kurzor je v levém horním rohu.")

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        return {"stopped": True, "active_run": self._active_run}

    def reset_stop(self) -> dict[str, Any]:
        self._stop.clear()
        return {"stopped": False}

    def focus(self, hwnd_value: Any) -> dict[str, Any]:
        self._require_windows()
        target = self._window(hwnd_value)
        hwnd = target["hwnd"]
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        current = int(user32.GetForegroundWindow() or 0)
        current_thread = int(user32.GetWindowThreadProcessId(current, None) or 0) if current else 0
        target_thread = int(user32.GetWindowThreadProcessId(hwnd, None) or 0)
        attached = False
        try:
            if current_thread and target_thread and current_thread != target_thread:
                attached = bool(user32.AttachThreadInput(current_thread, target_thread, True))
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetActiveWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(current_thread, target_thread, False)
        time.sleep(0.12)
        focused = int(user32.GetForegroundWindow() or 0) == hwnd
        if not focused:
            raise ComputerControlError(f"Windows nepovolil aktivovat okno „{target['title']}“.")
        return {"focused": True, "window": target}

    def capture(self, *, label: str = "observe", save: bool = True, hwnd: Any = None) -> dict[str, Any]:
        self._require_windows()
        try:
            from PIL import ImageGrab
        except ImportError as error:
            raise ComputerControlError("V portable Pythonu chybí Pillow pro snímání obrazovky.") from error
        captured_window = self._window(hwnd) if hwnd is not None else None
        if captured_window:
            bounds = captured_window["bounds"]
            image = ImageGrab.grab(bbox=(bounds["left"], bounds["top"], bounds["right"], bounds["bottom"]), all_screens=True)
        else:
            image = ImageGrab.grab(all_screens=True)
        digest = hashlib.sha256(image.tobytes()).hexdigest()
        path = ""
        if save:
            safe_label = "".join(char for char in label.lower() if char.isalnum() or char in "-_")[:32] or "screen"
            target = self.screenshot_dir / f"{datetime.now():%Y%m%d-%H%M%S-%f}-{safe_label}.jpg"
            image.convert("RGB").save(target, "JPEG", quality=82, optimize=True)
            path = str(target)
            self._prune_screenshots()
        foreground = next((item for item in self.windows() if item["foreground"]), None)
        return {
            "captured_at": datetime.now().isoformat(timespec="milliseconds"),
            "path": path, "sha256_pixels": digest, "width": image.width, "height": image.height,
            "foreground": foreground, "window": captured_window,
        }

    def _prune_screenshots(self, keep: int = 30) -> None:
        files = sorted(self.screenshot_dir.glob("*.jpg"), key=lambda path: path.stat().st_mtime, reverse=True)
        for stale in files[keep:]:
            try:
                stale.unlink()
            except OSError:
                pass

    def elements(self, hwnd_value: Any, limit: int = 300) -> dict[str, Any]:
        target = self._window(hwnd_value)
        limit = max(1, min(int(limit), 1000))
        backend = "win32"
        try:
            items = self._uia_worker.submit(self._elements_uia, target["hwnd"], limit).result(timeout=30)
            backend = "uia"
        except Exception:
            items = []
            @EnumChildProc
            def callback(hwnd: int, _lparam: int) -> bool:
                if len(items) >= limit:
                    return False
                bounds = _rect(hwnd)
                items.append({"hwnd": int(hwnd), "name": _window_text(hwnd)[:500],
                              "control_type": "win32", "class_name": _class_name(hwnd),
                              "enabled": bool(user32.IsWindowEnabled(hwnd)),
                              "visible": bool(user32.IsWindowVisible(hwnd)), "bounds": bounds})
                return True
            user32.EnumChildWindows(target["hwnd"], callback, 0)
        return {"window": target, "backend": backend, "elements": items, "count": len(items)}

    @staticmethod
    def _elements_uia(hwnd: int, limit: int) -> list[dict[str, Any]]:
        from pywinauto import Desktop

        items: list[dict[str, Any]] = []
        wrapper = Desktop(backend="uia").window(handle=hwnd)
        for element in wrapper.descendants()[:limit]:
            info = element.element_info
            rectangle = info.rectangle
            value = ""
            try:
                value = str(element.get_value() or "")[:1000]
            except Exception:
                try:
                    value = str(element.window_text() or "")[:1000]
                except Exception:
                    pass
            items.append({
                "name": str(info.name or "")[:500], "control_type": str(info.control_type or ""),
                "automation_id": str(info.automation_id or "")[:300],
                "class_name": str(info.class_name or "")[:300],
                "enabled": bool(info.enabled), "visible": bool(info.visible), "value": value,
                "bounds": {"left": rectangle.left, "top": rectangle.top,
                           "right": rectangle.right, "bottom": rectangle.bottom,
                           "width": rectangle.width(), "height": rectangle.height()},
            })
        return items

    def launch(self, program_value: Any, arguments: Any = None) -> dict[str, Any]:
        """Spustí EXE bez shellu; krátký název je povolen jen pro běžné aplikace Windows."""
        self._require_windows()
        program = str(program_value or "").strip()
        allowlisted = {"notepad.exe", "calc.exe", "mspaint.exe", "explorer.exe", "write.exe"}
        if not program:
            raise ComputerControlError("Chybí program ke spuštění.")
        candidate = Path(program)
        if candidate.is_absolute():
            candidate = candidate.resolve()
            if candidate.suffix.lower() != ".exe" or not candidate.is_file():
                raise ComputerControlError("Lze spustit pouze existující soubor EXE.")
            executable = str(candidate)
        else:
            if candidate.name.lower() not in allowlisted or candidate.name != program:
                raise ComputerControlError("Krátkým názvem lze spustit pouze povolenou aplikaci Windows.")
            executable = shutil.which(program) or program
        if arguments is None:
            arguments = []
        if not isinstance(arguments, list) or len(arguments) > 20:
            raise ComputerControlError("Argumenty programu musí být seznam nejvýše 20 hodnot.")
        clean_arguments = [str(value) for value in arguments]
        if any(len(value) > 2000 or "\x00" in value for value in clean_arguments):
            raise ComputerControlError("Argument programu je příliš dlouhý nebo neplatný.")
        process = subprocess.Popen([executable, *clean_arguments], close_fds=True)
        return {"launched": True, "pid": process.pid, "program": Path(executable).name,
                "argument_count": len(clean_arguments)}

    def _uia_element(self, target: dict[str, Any], selector: Any):
        if not isinstance(selector, dict):
            raise ComputerControlError("Selektor prvku musí být objekt.")
        try:
            from pywinauto import Desktop
        except ImportError as error:
            raise ComputerControlError("Pro sémantické ovládání prvků chybí pywinauto.") from error
        wrapper = Desktop(backend="uia").window(handle=target["hwnd"])
        elements = wrapper.descendants()
        try:
            if selector.get("index") is not None:
                index = int(selector["index"])
                if index < 0 or index >= len(elements):
                    raise ComputerControlError("Index prvku je mimo aktuální strom okna.")
                return elements[index]
        except (TypeError, ValueError) as error:
            raise ComputerControlError("Index prvku musí být celé číslo.") from error
        name = str(selector.get("name", "")).strip().casefold()
        automation_id = str(selector.get("automation_id", "")).strip().casefold()
        control_type = str(selector.get("control_type", "")).strip().casefold()
        if not any((name, automation_id, control_type)):
            raise ComputerControlError("Selektor musí obsahovat index, název, automation_id nebo typ prvku.")
        matches = []
        for element in elements:
            info = element.element_info
            if name and name not in str(info.name or "").casefold():
                continue
            if automation_id and automation_id != str(info.automation_id or "").casefold():
                continue
            if control_type and control_type != str(info.control_type or "").casefold():
                continue
            matches.append(element)
        if len(matches) != 1:
            raise ComputerControlError(f"Selektor prvku musí najít právě jeden prvek; nalezeno {len(matches)}.")
        return matches[0]

    def _uia_action(self, target: dict[str, Any], selector: Any, kind: str, text: str) -> dict[str, Any]:
        """Vyhledá i použije UIA prvek uvnitř vyhrazeného COM vlákna."""
        element = self._uia_element(target, selector)
        info = element.element_info
        if not bool(info.enabled) or not bool(info.visible):
            raise ComputerControlError("Vybraný prvek není viditelný a aktivní.")
        if kind == "element_click":
            try:
                element.invoke()
            except Exception:
                element.click_input()
            return {"type": kind, "element": str(info.name or info.control_type or "")[:200]}
        try:
            element.set_edit_text(text)
        except Exception:
            element.click_input()
            self._send([self._key_input(VK["ctrl"]), self._key_input(VK["a"]),
                        self._key_input(VK["a"], True), self._key_input(VK["ctrl"], True)])
            self._send(self._unicode_inputs(text))
        return {"type": kind, "element": str(info.name or info.control_type or "")[:200],
                "characters": len(text), "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}

    def _send(self, inputs: list[INPUT]) -> None:
        if not inputs:
            return
        array = (INPUT * len(inputs))(*inputs)
        sent = int(user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT)))
        if sent != len(inputs):
            raise ComputerControlError(f"Windows přijal pouze {sent} z {len(inputs)} vstupních událostí.")

    @staticmethod
    def _key_input(vk: int, key_up: bool = False) -> INPUT:
        return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if key_up else 0, 0, 0))

    def _unicode_inputs(self, text: str) -> list[INPUT]:
        result: list[INPUT] = []
        encoded = text.encode("utf-16-le")
        for index in range(0, len(encoded), 2):
            unit = int.from_bytes(encoded[index:index + 2], "little")
            result.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(0, unit, KEYEVENTF_UNICODE, 0, 0)))
            result.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0)))
        return result

    def _point(self, x_value: Any, y_value: Any, target: dict[str, Any]) -> tuple[int, int]:
        try:
            x, y = int(x_value), int(y_value)
        except (TypeError, ValueError) as error:
            raise ComputerControlError("Souřadnice myši musí být celá čísla.") from error
        screen = self.screen_bounds()
        if not (screen["left"] <= x < screen["right"] and screen["top"] <= y < screen["bottom"]):
            raise ComputerControlError("Souřadnice leží mimo virtuální plochu.")
        bounds = target["bounds"]
        if not (bounds["left"] <= x < bounds["right"] and bounds["top"] <= y < bounds["bottom"]):
            raise ComputerControlError("Souřadnice neleží uvnitř cílového okna.")
        return x, y

    def _move(self, x: int, y: int) -> None:
        screen = self.screen_bounds()
        dx = round((x - screen["left"]) * 65535 / max(1, screen["width"] - 1))
        dy = round((y - screen["top"]) * 65535 / max(1, screen["height"] - 1))
        self._send([INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(dx, dy, 0,
            MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, 0, 0))])

    def _perform(self, action: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
        kind = str(action.get("type", "")).strip().lower()
        self._check_stop()
        self.focus(target["hwnd"])
        if kind in {"click_relative", "double_click_relative", "right_click_relative", "drag_relative", "move_relative"}:
            bounds = target["bounds"]
            try:
                relative_x, relative_y = int(action.get("x")), int(action.get("y"))
            except (TypeError, ValueError) as error:
                raise ComputerControlError("Relativní souřadnice musí být celá čísla.") from error
            if not (0 <= relative_x < bounds["width"] and 0 <= relative_y < bounds["height"]):
                raise ComputerControlError("Relativní souřadnice neleží uvnitř cílového okna.")
            action = {**action, "type": kind.removesuffix("_relative"),
                      "x": bounds["left"] + relative_x, "y": bounds["top"] + relative_y}
            if kind == "drag_relative":
                try:
                    to_x, to_y = int(action.get("to_x")), int(action.get("to_y"))
                except (TypeError, ValueError) as error:
                    raise ComputerControlError("Cílové relativní souřadnice musí být celá čísla.") from error
                if not (0 <= to_x < bounds["width"] and 0 <= to_y < bounds["height"]):
                    raise ComputerControlError("Cílové relativní souřadnice neleží uvnitř okna.")
                action["to_x"], action["to_y"] = bounds["left"] + to_x, bounds["top"] + to_y
            kind = str(action["type"])
        if kind == "wait":
            seconds = max(0.0, min(float(action.get("seconds", 0.25)), 10.0))
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                self._check_stop()
                time.sleep(min(0.1, deadline - time.monotonic()))
            return {"type": kind, "seconds": seconds}
        if kind == "type_text":
            text = str(action.get("text", ""))
            if not text or len(text) > 8000:
                raise ComputerControlError("Text musí mít 1 až 8000 znaků.")
            self._send(self._unicode_inputs(text))
            return {"type": kind, "characters": len(text),
                    "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
        if kind in {"element_click", "element_set_value"}:
            text = str(action.get("text", ""))
            if len(text) > 8000:
                raise ComputerControlError("Hodnota prvku smí mít nejvýše 8000 znaků.")
            return self._uia_worker.submit(
                self._uia_action, target, action.get("selector"), kind, text,
            ).result(timeout=30)
        if kind in {"key", "chord"}:
            names = action.get("keys") if kind == "chord" else [action.get("key")]
            if not isinstance(names, list) or not names or len(names) > 5:
                raise ComputerControlError("Klávesová zkratka musí obsahovat 1 až 5 kláves.")
            codes: list[int] = []
            for name in names:
                code = VK.get(str(name).strip().lower())
                if code is None:
                    raise ComputerControlError(f"Nepodporovaná klávesa: {name}")
                codes.append(code)
            self._send([self._key_input(code) for code in codes] +
                       [self._key_input(code, True) for code in reversed(codes)])
            return {"type": kind, "keys": [str(name).lower() for name in names]}
        if kind in {"move", "click", "double_click", "right_click", "drag"}:
            x, y = self._point(action.get("x"), action.get("y"), target)
            self._move(x, y)
            if kind == "move":
                return {"type": kind, "x": x, "y": y}
            if kind == "drag":
                to_x, to_y = self._point(action.get("to_x"), action.get("to_y"), target)
                self._send([INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, 0))])
                self._move(to_x, to_y)
                self._send([INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, 0))])
                return {"type": kind, "x": x, "y": y, "to_x": to_x, "to_y": to_y}
            down, up = ((MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP) if kind == "right_click"
                        else (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP))
            repeats = 2 if kind == "double_click" else 1
            for _ in range(repeats):
                self._send([INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(0, 0, 0, down, 0, 0)),
                            INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(0, 0, 0, up, 0, 0))])
                if repeats == 2:
                    time.sleep(0.08)
            return {"type": kind, "x": x, "y": y}
        if kind == "scroll":
            amount = max(-20, min(int(action.get("amount", 0)), 20))
            if amount == 0:
                raise ComputerControlError("Posun kolečka nesmí být nula.")
            self._send([INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(0, 0, amount * 120, MOUSEEVENTF_WHEEL, 0, 0))])
            return {"type": kind, "amount": amount}
        raise ComputerControlError(f"Nepodporovaný typ počítačové akce: {kind or 'prázdný'}")

    def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_windows()
        actions = payload.get("actions")
        if not isinstance(actions, list) or not actions or len(actions) > 100:
            raise ComputerControlError("Sekvence musí obsahovat 1 až 100 akcí.")
        run_id = uuid4().hex
        started = time.monotonic()
        with self._lock:
            if self._stop.is_set() and payload.get("reset_stop") is not True:
                raise ComputerControlError("Nouzové zastavení je aktivní; nejdřív jej vědomě resetuj.")
            if payload.get("reset_stop") is True:
                self._stop.clear()
            target = self._window(payload.get("hwnd"))
            self._active_run = run_id
            before = self.capture(label=f"{run_id}-before", hwnd=target["hwnd"])
            completed: list[dict[str, Any]] = []
            success = False
            error_text = ""
            try:
                for index, action in enumerate(actions):
                    if not isinstance(action, dict):
                        raise ComputerControlError(f"Akce {index + 1} není objekt.")
                    completed.append(self._perform(action, target))
                    time.sleep(max(0.01, min(float(action.get("pause_after", 0.08)), 2.0)))
                success = True
            except (ComputerControlError, OSError, TypeError, ValueError) as error:
                error_text = str(error)
            after = self.capture(label=f"{run_id}-after", hwnd=target["hwnd"])
            result = {
                "run_id": run_id, "success": success, "error": error_text,
                "window": target, "completed": completed, "requested_count": len(actions),
                "duration_ms": int((time.monotonic() - started) * 1000),
                "before": before, "after": after,
                "screen_changed": before["sha256_pixels"] != after["sha256_pixels"],
            }
            self._audit(result)
            self._last_result = result
            self._active_run = ""
            if not success:
                raise ComputerControlError(error_text)
            return result

    def verify(self, instruction: str, hwnd_value: Any, execution: dict[str, Any]) -> dict[str, Any]:
        """Ověří pozorovatelný výsledek; nezaměňuje změnu obrazu za splnění cíle."""
        target = self._window(hwnd_value)
        exact = re.search(
            r"(?is)\b(?:napiš|napis|zadej|vlož|vloz)\s+(?:přesně\s+|presne\s+)?(?:text\s+)?[\"„“]?(.+?)[\"„“]?\s+(?:do|v)\s+",
            str(instruction),
        )
        if exact:
            expected = exact.group(1).strip().strip('\"„“')
            observation = self.elements(target["hwnd"], 300)
            values = [str(item["value"]) for item in observation.get("elements", [])
                      if item.get("control_type") in {"Edit", "Document"}
                      and item.get("visible") is True and item.get("value") is not None]
            matched = bool(expected) and expected in values
            # Některé UI toolkity neposkytují ValuePattern. Jestli se přesný
            # text po akci nově objeví v titulku cílového okna, je to stále
            # konkrétní pozorovatelný důkaz, ne pouhá změna pixelů.
            previous_title = str((execution.get("window") or {}).get("title", ""))
            current_title = str(target.get("title", ""))
            title_matched = bool(expected) and expected not in previous_title and expected in current_title
            matched = matched or title_matched
            return {"achieved": matched, "kind": "uia-value", "expected_sha256": hashlib.sha256(expected.encode("utf-8")).hexdigest(),
                    "evidence": "window-title" if title_matched else "uia-value" if matched else "none",
                    "message": "Přesný text byl nalezen v cílovém okně." if matched else "Přesný text po provedení nebyl v cílovém okně nalezen."}
        changed = bool(execution.get("screen_changed"))
        return {"achieved": False, "kind": "unverified", "screen_changed": changed,
                "message": "Akce byly odeslány, ale splnění cíle nebylo ověřeno. Změna obrazu sama nestačí; další akce se automaticky neopakují."}

    def _audit(self, result: dict[str, Any]) -> None:
        record = {
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            "run_id": result["run_id"], "success": result["success"],
            "error": result["error"][:1000], "duration_ms": result["duration_ms"],
            "target": {key: result["window"].get(key) for key in ("hwnd", "title", "pid", "process")},
            "actions": result["completed"], "requested_count": result["requested_count"],
            "screen_changed": result["screen_changed"],
            "before_hash": result["before"]["sha256_pixels"],
            "after_hash": result["after"]["sha256_pixels"],
        }
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def status(self) -> dict[str, Any]:
        pywinauto_available = False
        try:
            import pywinauto  # noqa: F401
            pywinauto_available = True
        except ImportError:
            pass
        return {
            "available": IS_WINDOWS, "platform": os.name, "active_run": self._active_run,
            "emergency_stopped": self._stop.is_set(), "screenshot_available": self._pillow_available(),
            "uia_available": pywinauto_available, "audit_path": str(self.audit_path),
            "last_result": self._last_result,
            "capabilities": ["screenshots", "windows", "focus", "application-launch", "mouse", "keyboard",
                             "semantic-elements", "sequences", "before-after-verification", "emergency-stop",
                             "audit", "accessibility-tree", "window-relative-visual-actions"],
        }

    @staticmethod
    def _pillow_available() -> bool:
        try:
            import PIL  # noqa: F401
            return True
        except ImportError:
            return False


COMPUTER = ComputerController(Path(__file__).resolve().parent)
