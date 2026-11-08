"""可序列化的处理命令：UI / 处理链 / 脚本 / CLI 共用同一种数据形态。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Command:
    """一次处理操作（op + 参数）。params 只允许 JSON 基本类型。"""

    op: str
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"op": self.op, "params": dict(self.params)}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Any) -> "Command":
        if not isinstance(data, dict):
            raise ValueError(f"命令必须是 dict，收到 {type(data).__name__}")
        op = data.get("op")
        if not isinstance(op, str) or not op:
            raise ValueError("命令缺少 op 字段")
        params = data.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise ValueError(f"命令参数必须是 dict，收到 {type(params).__name__}")
        return cls(op=op, params=dict(params))

    @classmethod
    def from_json(cls, text: str) -> "Command":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"命令 JSON 解析失败: {exc}") from exc
        return cls.from_dict(data)

    @classmethod
    def from_step(cls, step: Any) -> "Command":
        """兼容旧的 {'op':..., 'params':{...}} 步骤与 Command 实例。"""
        if isinstance(step, cls):
            return step
        return cls.from_dict(step)


def commands_to_json(commands: list[Command]) -> str:
    return json.dumps([c.to_dict() for c in commands], ensure_ascii=False, indent=2)


def commands_from_json(text: str) -> list[Command]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"命令列表 JSON 解析失败: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError("命令列表必须是 JSON 数组")
    return [Command.from_dict(item) for item in data]


def command_label(command: Command) -> str:
    from core.commands.spec import get_spec

    try:
        spec = get_spec(command.op)
    except KeyError:
        return command.op
    params = command.params
    if not params:
        return spec.label
    text = "，".join(f"{k}={v:g}" if isinstance(v, (int, float)) else f"{k}={v}"
                     for k, v in params.items())
    return f"{spec.label}（{text}）"
