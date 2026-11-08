"""处理链：把多个处理命令串成流水线，GUI / 脚本 / CLI 共用同一实现。

步骤格式：{"op": "voxel", "params": {...}} 或 `Command` 实例，依次作用在当前点云上，
每一步产出新的 CloudEntity（非破坏性），字段（RGB/强度/标签）随之裁切/合并。

命令的标签、参数默认值与校验集中在 core.commands，本模块只负责编排与进度。
"""

from __future__ import annotations

from core.cancellation import CancelCb, check_cancel
from core.commands import Command, run_command
from core.commands.spec import get_spec, labels
from core.model.entities import CloudEntity
from core.progress import ProgressCb, report_progress as _report

STEP_LABELS = labels()


def apply_step(entity: CloudEntity, step, progress: ProgressCb = None,
               cancel: CancelCb = None) -> CloudEntity:
    """执行单个步骤（dict 或 Command），返回新的点云。"""
    return run_command(entity, Command.from_step(step), progress, cancel)


def run_pipeline(entity: CloudEntity, steps: list, progress: ProgressCb = None,
                 cancel: CancelCb = None) -> CloudEntity:
    """按顺序执行处理链，返回最终点云（info 记录完整步骤摘要）。"""
    if not steps:
        raise ValueError("处理链为空")
    current = entity
    infos = []
    for index, step in enumerate(steps):
        start = index / len(steps)
        span = 1.0 / len(steps)

        def cb(fraction, _start=start, _span=span):
            _report(progress, _start + fraction * _span)

        check_cancel(cancel)
        current = apply_step(current, step, cb, cancel=cancel)
        infos.append(current.info)
    current.info = " → ".join(infos)
    _report(progress, 1.0)
    return current


# ---------------- 命令行友好解析 ----------------
def parse_stage(text: str) -> dict:
    """解析 `op:arg1,arg2` 形式的命令行步骤，例如 sor:20,2.0 / voxel:0.5。"""
    if ":" not in text:
        raise ValueError(f"步骤格式应为 op:参数，例如 voxel:0.5（收到 {text!r}）")
    op, raw = text.split(":", 1)
    op = op.strip().lower()
    try:
        spec = get_spec(op)
    except KeyError:
        raise ValueError(f"未知步骤 {op}，可用：{', '.join(STEP_LABELS)}") from None
    args = [a.strip() for a in raw.split(",") if a.strip() != ""] if raw else []
    if len(args) > len(spec.params):
        raise ValueError(f"{op} 最多 {len(spec.params)} 个参数")

    params = {}
    for param, value in zip(spec.params, args):
        try:
            params[param.key] = param.coerce(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{op} 参数 {param.key} 无效: {value!r}（{exc}）") from None
    return {"op": op, "params": params}
