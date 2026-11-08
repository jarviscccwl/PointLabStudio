"""进度回调约定：算法 / IO / 处理链共用同一类型与安全上报方式。"""

from __future__ import annotations

from typing import Callable, Optional

import numpy as np

ProgressCb = Optional[Callable[[float], None]]


def report_progress(progress: ProgressCb, value: float) -> None:
    """把 0..1 之间的进度裁剪后上报；没有回调时静默忽略。"""
    if progress is not None:
        progress(float(np.clip(value, 0.0, 1.0)))
