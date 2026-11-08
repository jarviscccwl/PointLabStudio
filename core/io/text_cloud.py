"""Bounded numeric-text previews and explicit, read-only point-cloud conversion."""
from __future__ import annotations

import csv
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re

import numpy as np

from core.cancellation import check_cancel
from core.model.entities import CloudEntity, ScalarField


@dataclass
class TextCloudPreview:
    columns: list[str]
    rows: list[list[str]]
    delimiter: str | None
    skip_rows: int
    has_header: bool
    sample_bytes: int


def _split(line, delimiter):
    return line.split() if delimiter is None else next(csv.reader([line], delimiter=delimiter))


def _delimiter(line):
    try:
        return csv.Sniffer().sniff(line, delimiters=",\t;").delimiter
    except csv.Error:
        return None


def _unique_names(names):
    result, used = [], set()
    for number, value in enumerate(names, 1):
        name = str(value).strip() or f"Column {number}"
        candidate, suffix = name, 2
        while candidate in used:
            candidate = f"{name}_{suffix}"
            suffix += 1
        result.append(candidate)
        used.add(candidate)
    return result


def preview_text_cloud(path, *, delimiter="auto", header=None, max_rows=30,
                       max_bytes=65536) -> TextCloudPreview:
    """Inspect at most max_bytes bytes without loading the full source.

    delimiter=None means whitespace. header=None detects a named first row or
    a '# columns:' comment. Explicit True/False overrides the header decision.
    """
    with open(path, "rb") as handle:
        sample = handle.read(max_bytes)
    text = sample.decode("utf-8-sig", errors="replace")
    if os.path.getsize(path) > len(sample) and not text.endswith(("\n", "\r")):
        text = text.rsplit("\n", 1)[0] if "\n" in text else ""
    data, comment_columns = [], None
    for line_number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            match = re.match(r"#\s*columns\s*:\s*(.+)", line, flags=re.IGNORECASE)
            if match and not data:
                comment_columns = match.group(1)
            continue
        data.append((line_number, line))
        if len(data) >= max_rows + 1:
            break
    if not data:
        raise ValueError("预览范围内没有可用的数据行。")
    detected = _delimiter(data[0][1]) if delimiter == "auto" else delimiter
    first = _split(data[0][1], detected)
    width = len(first)
    if width > 256:
        raise ValueError("点云列映射预览最多支持 256 列。")
    try:
        [float(value) for value in first]
        numeric_first = True
    except ValueError:
        numeric_first = False
    skip_rows = 0
    if comment_columns and header is not False:
        names = _split(comment_columns, detected)
        for alternate in (",", "\t", ";", None):
            if len(names) == width:
                break
            names = _split(comment_columns, alternate)
        if len(names) != width:
            raise ValueError("# columns 声明的列数与数据行不一致。")
        has_header = True
    elif header is True or (header is None and not numeric_first):
        names = first
        skip_rows = data.pop(0)[0]
        has_header = True
    else:
        names = [f"Column {number + 1}" for number in range(width)]
        has_header = False
    rows = [_split(line, detected) for _, line in data[:max_rows]]
    if not rows:
        raise ValueError("表头之后没有可预览的数据行。")
    if width < 3:
        raise ValueError("导入点云至少需要三列数据。")
    return TextCloudPreview(_unique_names(names), rows, detected, skip_rows,
                            has_header, len(sample))


def read_text_cloud(path, options, cancel=None) -> CloudEntity:
    """Read all numeric rows using explicit zero-based x/y/z column mappings.

    options has delimiter (None for whitespace), skip_rows, optional columns
    names and mandatory x/y/z indices. Every numeric column remains a scalar
    field. Invalid rows are reported with their physical source line number.
    """
    check_cancel(cancel)
    axes = [options.get(axis) for axis in ("x", "y", "z")]
    if any(not isinstance(index, int) or index < 0 for index in axes) or len(set(axes)) != 3:
        raise ValueError("请为 X、Y、Z 选择三个不同的数据列映射。")
    delimiter = options.get("delimiter")
    if delimiter is not None and (not isinstance(delimiter, str) or len(delimiter) != 1):
        raise ValueError("分隔符必须为单个字符或空白分隔。")
    skip_rows = int(options.get("skip_rows", 0))
    if skip_rows < 0:
        raise ValueError("跳过行数不能为负。")
    names = list(options.get("columns") or [])
    width = len(names) or None
    chunks, pending = [], []
    with open(path, "r", encoding="utf-8-sig") as handle:
        for line_number, raw in enumerate(handle, 1):
            if line_number % 1024 == 0:
                check_cancel(cancel)
            line = raw.strip()
            if line_number <= skip_rows or not line or line.startswith("#"):
                continue
            try:
                cells = _split(line, delimiter)
            except csv.Error as exc:
                raise ValueError(f"第 {line_number} 行无法按所选分隔符读取。") from exc
            if width is None:
                width = len(cells)
                names = [f"Column {number + 1}" for number in range(width)]
            if len(cells) != width or max(axes) >= width:
                raise ValueError(f"第 {line_number} 行列数与映射不一致，预期 {width} 列。")
            try:
                values = [float(value) for value in cells]
            except ValueError as exc:
                raise ValueError(f"第 {line_number} 行包含非数值单元格。") from exc
            coordinates = np.asarray(values)[axes]
            if not np.all(np.isfinite(coordinates)) or np.any(np.abs(coordinates) > np.finfo(np.float32).max):
                raise ValueError(f"第 {line_number} 行的坐标包含无效数值或超出范围。")
            pending.append(values)
            if len(pending) == 8192:
                chunks.append(np.asarray(pending, dtype=np.float64))
                pending.clear()
    check_cancel(cancel)
    if pending:
        chunks.append(np.asarray(pending, dtype=np.float64))
    if not chunks:
        raise ValueError("没有可导入的数值数据行。")
    values = np.concatenate(chunks, axis=0)
    names = _unique_names(names)
    fields = {name: ScalarField(name, np.ascontiguousarray(values[:, index]))
              for index, name in enumerate(names)}
    absolute_path = os.path.abspath(os.fspath(path))
    cloud = CloudEntity(Path(path).stem, path=absolute_path,
                        xyz=np.ascontiguousarray(values[:, axes], dtype=np.float32), fields=fields,
                        color_mode="intensity" if "intensity" in fields else "height",
                        info=json.dumps(dict(source=absolute_path, delimiter=delimiter,
                                             skip_rows=skip_rows, columns=names,
                                             mapping=dict(zip(("x", "y", "z"), axes))), ensure_ascii=False))
    check_cancel(cancel)
    return cloud
