"""命令元数据：标签、参数默认值/类型/范围，供 UI 对话框、CLI 校验与文档复用。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple

from core.cancellation import CancelCb
from core.model.entities import CloudEntity
from core.progress import ProgressCb

ApplyFn = Callable[[CloudEntity, dict, ProgressCb, CancelCb], CloudEntity]


@dataclass(frozen=True)
class ParamDef:
    key: str
    label: str
    kind: str = "float"          # float / int / choice
    default: Any = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    decimals: int = 2
    options: Tuple[Any, ...] = ()

    def to_dialog_field(self) -> dict:
        data = {"key": self.key, "label": self.label, "kind": self.kind,
                "default": self.default}
        if self.minimum is not None:
            data["min"] = self.minimum
        if self.maximum is not None:
            data["max"] = self.maximum
        if self.kind == "float":
            data["decimals"] = self.decimals
        if self.options:
            data["options"] = list(self.options)
        return data

    def coerce(self, value: Any) -> Any:
        if self.kind == "int":
            return int(float(value))
        if self.kind == "float":
            return float(value)
        if self.kind == "choice":
            if value not in self.options:
                raise ValueError(f"{self.label} 只能是 {self.options} 之一，收到 {value!r}")
            return value
        return value

    def validate(self, value: Any) -> Any:
        value = self.coerce(value)
        if self.kind in ("int", "float"):
            if self.minimum is not None and value < self.minimum:
                raise ValueError(f"{self.label} 不能小于 {self.minimum:g}（收到 {value:g}）")
            if self.maximum is not None and value > self.maximum:
                raise ValueError(f"{self.label} 不能大于 {self.maximum:g}（收到 {value:g}）")
        return value


@dataclass(frozen=True)
class CommandSpec:
    op: str
    label: str
    params: Tuple[ParamDef, ...]
    apply: ApplyFn
    hint: str = ""

    def defaults(self) -> dict:
        return {p.key: p.default for p in self.params}

    def _by_key(self) -> dict:
        return {p.key: p for p in self.params}

    def normalize(self, params: Optional[dict]) -> dict:
        raw = dict(params or {})
        known = self._by_key()
        unknown = [k for k in raw if k not in known]
        if unknown:
            raise ValueError(f"{self.label} 不支持参数: {', '.join(unknown)}")
        out = {}
        for key, spec in known.items():
            value = raw.get(key, spec.default)
            out[key] = spec.validate(value)
        return out

    def dialog_fields(self) -> list:
        return [p.to_dialog_field() for p in self.params]


_REGISTRY: dict[str, CommandSpec] = {}


def register(spec: CommandSpec) -> CommandSpec:
    if spec.op in _REGISTRY:
        raise ValueError(f"命令已注册: {spec.op}")
    _REGISTRY[spec.op] = spec
    return spec


def get_spec(op: str) -> CommandSpec:
    try:
        return _REGISTRY[op]
    except KeyError:
        available = ", ".join(_REGISTRY) or "（无）"
        raise KeyError(f"未知命令 {op!r}，可用：{available}") from None


def specs() -> dict:
    return dict(_REGISTRY)


def labels() -> dict:
    return {op: spec.label for op, spec in _REGISTRY.items()}
