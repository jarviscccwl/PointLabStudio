"""命令历史：撤销 / 重做，并保留可序列化的处理命令记录。

UI 的撤销仍依赖闭包（简单可靠，能覆盖显示/加载/裁剪等非点云操作）；
产生点云的处理操作会附带 `Command`，可导出为脚本 / 处理链 JSON。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from core.commands.command import Command, commands_to_json


@dataclass
class HistoryEntry:
    label: str
    undo: Callable[[], None]
    redo: Callable[[], None]
    command: Optional[Command] = None

    def to_dict(self) -> dict:
        data = {"label": self.label}
        if self.command is not None:
            data["command"] = self.command.to_dict()
        return data


class History:
    def __init__(self, limit: int = 50):
        self.limit = limit
        self._undo_stack: list[HistoryEntry] = []
        self._redo_stack: list[HistoryEntry] = []

    def push(self, label: str, undo: Callable[[], None], redo: Callable[[], None],
             command: Optional[Command] = None):
        self._undo_stack.append(HistoryEntry(label, undo, redo, command))
        if len(self._undo_stack) > self.limit:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def undo(self) -> Optional[str]:
        if not self._undo_stack:
            return None
        entry = self._undo_stack[-1]
        if entry.undo() is False:
            return None
        self._undo_stack.pop()
        self._redo_stack.append(entry)
        return entry.label

    def redo(self) -> Optional[str]:
        if not self._redo_stack:
            return None
        entry = self._redo_stack[-1]
        if entry.redo() is False:
            return None
        self._redo_stack.pop()
        self._undo_stack.append(entry)
        return entry.label

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    # ---------------- 命令记录 ----------------
    def commands(self) -> list[Command]:
        """按执行顺序返回仍可撤销的处理命令（不含显示/加载等 UI 操作）。"""
        return [e.command for e in self._undo_stack if e.command is not None]

    def commands_json(self) -> str:
        return commands_to_json(self.commands())

    def entries(self) -> list[dict]:
        return [e.to_dict() for e in self._undo_stack]

    def clear(self):
        self._undo_stack.clear()
        self._redo_stack.clear()
