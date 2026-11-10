"""Bounded, CPU-only view composition and atomic GIF/MP4 sequence export.

All frame arrays are uint8 RGB; sizes are (width, height). Call ``append``
and ``close`` from one worker, and call ``abort`` in that worker on cancellation.
Only a successful close replaces the destination. No Qt or display is required.
"""
from __future__ import annotations

import math
import numbers
import os
from pathlib import Path
import shutil
import tempfile

import numpy as np
from PIL import GifImagePlugin, Image, ImageDraw


def export_file_paths(target, format: str, first: int, last: int) -> tuple[Path, ...]:
    """Name only selected outputs, using one-based frame numbers for batches.

    ``target`` is a user-chosen file name, also used as the batch prefix.
    Ranges are zero-based and inclusive. An omitted extension is appended;
    arbitrary dots in a name are preserved. The first path is the file to
    reveal after export. No directory or companion metadata is generated.
    """
    if format not in ('mp4', 'gif', 'png', 'ply', 'pcd', 'png_ply'):
        raise ValueError('不支持的导出格式')
    if not 0 <= first <= last:
        raise ValueError('请选择有效的起止帧区间')
    target = Path(target)
    extensions = ('png', 'ply') if format == 'png_ply' else (format,)
    extension = '.' + extensions[0]
    if target.suffix.lower() != extension:
        target = target.with_name(target.name + extension)
    if format in ('mp4', 'gif'):
        return (target,)
    return tuple(
        target.with_name(f'{target.stem}{"" if first == last else f"_{index + 1:06d}"}.{suffix}')
        for index in range(first, last + 1) for suffix in extensions
    )


class ExportRollbackError(RuntimeError):
    """An output could not be restored; retain the staging recovery directory."""

    def __init__(self, staging, cause, errors):
        self.recovery_directory = Path(staging)
        super().__init__(f'导出未完成：{cause}；部分原文件未能恢复：{"；".join(errors)}。'
                         f'原文件备份保留在：{self.recovery_directory}')


def publish_export_files(staging, paths, *, overwrite=False, cancelled=None) -> bool:
    """Publish fully written files, rolling back all outputs on error/cancel.

    Each destination's basename must exist in ``staging``. Default publication
    never overwrites, including a destination created after the initial check.
    Explicit overwrites retain backups until the whole set succeeds. Returns
    False for cancellation. The caller removes staging after success or an
    ordinary failure, but must retain it when ExportRollbackError is raised.
    """
    staging, paths = Path(staging), tuple(Path(path) for path in paths)
    is_cancelled = cancelled or (lambda: False)
    if is_cancelled():
        return False
    if not paths or len({path.name for path in paths}) != len(paths):
        raise ValueError('导出文件名为空或重复')
    for path in paths:
        if not (staging / path.name).is_file():
            raise FileNotFoundError(f'导出文件不完整：{path.name}')
        if path.is_dir() or ((path.exists() or path.is_symlink()) and not overwrite):
            raise FileExistsError(f'文件已存在：{path}')
        if not path.parent.is_dir():
            raise FileNotFoundError(f'保存位置不存在：{path.parent}')
    published = []
    try:
        for path in paths:
            if is_cancelled():
                break
            source, backup = staging / path.name, None
            if overwrite:
                if path.exists() or path.is_symlink():
                    if path.is_dir():
                        raise FileExistsError(f'保存位置是文件夹：{path}')
                    backup = staging / '.previous' / path.name
                    backup.parent.mkdir(exist_ok=True)
                    shutil.copy2(path, backup, follow_symlinks=False)
                os.replace(source, path)
            elif os.name == 'nt':
                # Windows rename is atomic and fails if the destination exists.
                os.rename(source, path)
            else:
                # POSIX rename may overwrite: a hard link provides no-clobber
                # publication on the same filesystem as the staging directory.
                os.link(source, path)
            published.append((path, backup))
        else:
            if not is_cancelled():
                return True
    except Exception as exc:
        cause = exc
    else:
        cause = None
    errors = []
    for path, backup in reversed(published):
        try:
            if backup is None:
                path.unlink(missing_ok=True)
            else:
                os.replace(backup, path)
        except OSError as exc:
            errors.append(f'{path.name}：{exc}')
    if errors:
        raise ExportRollbackError(staging, cause or '已取消', errors) from cause
    if cause is not None:
        raise cause
    return False


def _size(value: tuple[int, int]) -> tuple[int, int]:
    if (not isinstance(value, (tuple, list)) or len(value) != 2
            or any(isinstance(v, bool) or not isinstance(v, numbers.Integral)
                   or v <= 0 for v in value)):
        raise ValueError("输出尺寸必须是两个正整数（宽度，高度）。")
    return int(value[0]), int(value[1])


def _rgb(value: np.ndarray) -> np.ndarray:
    if (not isinstance(value, np.ndarray) or value.dtype != np.uint8
            or value.ndim != 3 or value.shape[2] != 3
            or value.shape[0] == 0 or value.shape[1] == 0):
        raise ValueError("画面必须是非空的 uint8 RGB 数组（高度，宽度，3）。")
    return value


def compose_views(
    cloud_rgb: np.ndarray | None,
    image_rgb: np.ndarray | None,
    layout: str = "paired",
    size: tuple[int, int] = (1280, 720),
) -> np.ndarray:
    """Fit fresh views into a canvas, preserving their aspect ratios.

    ``paired`` puts cloud left and image right. ``cloud`` and ``image`` use
    the entire canvas. A missing selected view gets a neutral, marked blank;
    the function has no previous-frame state and never mutates either input.
    """
    width, height = _size(size)
    if layout not in ("paired", "cloud", "image"):
        raise ValueError("画面布局必须是 paired、cloud 或 image。")
    if layout == "paired" and width < 2:
        raise ValueError("双窗格输出尺寸的宽度至少为 2。")
    canvas = Image.new("RGB", (width, height), (24, 24, 24))
    if layout == "paired":
        split = width // 2
        panels = ((cloud_rgb, 0, split, "NO CLOUD FRAME"),
                  (image_rgb, split, width - split, "NO IMAGE FRAME"))
    elif layout == "cloud":
        panels = ((cloud_rgb, 0, width, "NO CLOUD FRAME"),)
    else:
        panels = ((image_rgb, 0, width, "NO IMAGE FRAME"),)
    for frame, left, panel_width, label in panels:
        if frame is None:
            _draw_missing(canvas, left, panel_width, height, label)
            continue
        frame = _rgb(frame)
        frame_height, frame_width = frame.shape[:2]
        scale = min(panel_width / frame_width, height / frame_height)
        fitted_size = (max(1, min(panel_width, round(frame_width * scale))),
                       max(1, min(height, round(frame_height * scale))))
        with Image.fromarray(frame) as source:
            fitted = source.resize(fitted_size, Image.Resampling.LANCZOS)
            try:
                canvas.paste(fitted, (left + (panel_width - fitted_size[0]) // 2,
                                      (height - fitted_size[1]) // 2))
            finally:
                fitted.close()
    try:
        return np.array(canvas, dtype=np.uint8, copy=True)
    finally:
        canvas.close()


def _draw_missing(canvas: Image.Image, left: int, width: int, height: int, label: str) -> None:
    """Use a font-independent crossed frame with a small built-in text label."""
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((left, 0, left + width - 1, height - 1), fill=(32, 32, 32))
    if width < 12 or height < 12:
        return
    margin = max(3, min(width, height) // 8)
    x0, x1 = left + margin, left + width - margin - 1
    y0, y1 = margin, height - margin - 1
    draw.rectangle((x0, y0, x1, y1), outline=(72, 72, 72))
    draw.line((x0, y0, x1, y1), fill=(48, 48, 48))
    draw.line((x0, y1, x1, y0), fill=(48, 48, 48))
    bbox = draw.textbbox((0, 0), label)
    label_width, label_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if label_width + 12 <= width and label_height + 12 <= height:
        x, y = left + (width - label_width) // 2, (height - label_height) // 2
        draw.rectangle((x - 4, y - 3, x + label_width + 4, y + label_height + 3),
                       fill=(32, 32, 32))
        draw.text((x - bbox[0], y - bbox[1]), label, fill=(168, 168, 168))


class SequenceWriter:
    """Stream RGB frames into a temporary sibling file and publish atomically.

    MP4 uses OpenCV's ``mp4v`` codec and requires even dimensions. GIF has a
    separate palette for every frame, 256-color quantization, and 10 ms timing
    precision. Its supported frame-rate range is 100/65535 through 100 fps.
    Cumulative rounding keeps GIF timing within 5 ms of the requested duration.
    Neither encoder retains the sequence in RAM; input arrays can be reused
    immediately after ``append`` returns. Errors make the writer unusable.
    """

    def __init__(self, path: str, fps: float, size: tuple[int, int]):
        self._temporary: Path | None = None
        self._encoder = None
        self._stream = None
        self._state = "aborted"
        self.path = Path(path)
        self.size = _size(size)
        try:
            self.fps = float(fps)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("帧率必须是有限的正数。") from exc
        if isinstance(fps, bool) or not math.isfinite(self.fps) or self.fps <= 0:
            raise ValueError("帧率必须是有限的正数。")
        self._format = self.path.suffix.lower()
        if self._format not in (".mp4", ".gif"):
            raise ValueError("序列导出仅支持 .mp4 或 .gif 文件。")
        if self._format == ".mp4" and any(dimension % 2 for dimension in self.size):
            raise ValueError("MP4 输出尺寸的宽度和高度必须都是偶数。")
        if self._format == ".gif":
            if max(self.size) > 65535:
                raise ValueError("GIF 输出尺寸的宽度和高度不能超过 65535。")
            if not 100 / 65535 <= self.fps <= 100:
                raise ValueError("GIF 帧率必须介于 100/65535 和 100 fps 之间。")
        self.frame_count = 0
        self._gif_ticks = 0
        try:
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=f".tmp{self._format}",
                dir=str(self.path.parent),
            )
            self._temporary = Path(temporary)
            os.close(descriptor)
            if self._format == ".mp4":
                import cv2

                self._encoder = cv2.VideoWriter(
                    str(self._temporary), cv2.VideoWriter_fourcc(*"mp4v"), self.fps, self.size,
                )
                if not self._encoder.isOpened():
                    raise RuntimeError("当前环境无法创建 MP4 编码器（mp4v），请改用 GIF。")
            else:
                self._stream = self._temporary.open("wb")
            self._state = "open"
        except Exception as exc:
            self._fail(exc, "无法创建导出文件")

    def append(self, rgb: np.ndarray) -> None:
        """Write one frame synchronously; a failed append aborts the export."""
        if self._state != "open":
            raise RuntimeError("导出已关闭或取消，不能继续写入画面。")
        try:
            rgb = _rgb(rgb)
            if (rgb.shape[1], rgb.shape[0]) != self.size:
                raise ValueError(f"画面尺寸与导出尺寸 {self.size[0]} × {self.size[1]} 不一致。")
            if self._format == ".mp4":
                self._encoder.write(np.ascontiguousarray(rgb[..., ::-1]))
            else:
                self._append_gif(rgb)
            self.frame_count += 1
        except Exception as exc:
            self._fail(exc, "写入导出画面失败")

    def _append_gif(self, rgb: np.ndarray) -> None:
        with Image.fromarray(rgb) as source:
            palette_frame = source.quantize(colors=256, method=Image.Quantize.MEDIANCUT,
                                            dither=Image.Dither.NONE)
        try:
            if self.frame_count == 0:
                header, _ = GifImagePlugin.getheader(palette_frame, info={"loop": 0})
                self._stream.writelines(header)
            ticks = math.floor((self.frame_count + 1) * 100 / self.fps + 0.5)
            # getdata holds only the compressed bytes for this single frame.
            chunks = GifImagePlugin.getdata(
                palette_frame, duration=(ticks - self._gif_ticks) * 10, disposal=2,
                include_color_table=self.frame_count > 0,
            )
            self._stream.writelines(chunks)
            self._gif_ticks = ticks
        finally:
            palette_frame.close()

    def close(self, commit: bool = True) -> None:
        """Finish and replace the destination, or discard when commit=False.

        Repeated successful closes are harmless. Closing an aborted writer
        with commit=True raises; an empty sequence is never published.
        """
        if not commit:
            self.abort()
            return
        if self._state == "closed":
            return
        if self._state != "open":
            raise RuntimeError("导出已取消或失败，无法保存结果。")
        try:
            if self.frame_count == 0:
                raise ValueError("没有可导出的画面帧，未生成文件。")
            if self._encoder is not None:
                self._encoder.release()
                self._encoder = None
                self._check_mp4()
                with self._temporary.open("r+b") as output:
                    output.flush()
                    os.fsync(output.fileno())
            else:
                self._stream.write(b";")
                self._stream.flush()
                os.fsync(self._stream.fileno())
                self._stream.close()
                self._stream = None
            os.replace(self._temporary, self.path)
            self._temporary = None
            self._state = "closed"
        except Exception as exc:
            self._fail(exc, "保存导出文件失败")

    def _check_mp4(self) -> None:
        """Catch codec failures and silent frame drops before replacing a result."""
        import cv2

        capture = cv2.VideoCapture(str(self._temporary))
        try:
            if not capture.isOpened():
                raise RuntimeError("MP4 编码结果无法打开。")
            count = round(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            ok, first = capture.read()
            if count != self.frame_count or not ok or first.shape[:2] != self.size[::-1]:
                raise RuntimeError("MP4 编码后的帧数或尺寸不正确，未发布文件。")
        finally:
            capture.release()

    def abort(self) -> None:
        """Discard only this writer's temporary file; never remove the destination."""
        if self._state == "closed":
            return
        self._state = "aborted"
        errors = []
        if self._encoder is not None:
            try:
                self._encoder.release()
            except Exception as exc:
                errors.append(exc)
            finally:
                self._encoder = None
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception as exc:
                errors.append(exc)
            finally:
                self._stream = None
        if self._temporary is not None:
            try:
                self._temporary.unlink(missing_ok=True)
                self._temporary = None
            except OSError as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError(f"取消导出时未能完整清理临时文件：{errors[0]}") from errors[0]

    def _fail(self, error: Exception, message: str) -> None:
        try:
            self.abort()
        except Exception as cleanup_error:
            raise RuntimeError(f"{message}：{error}；{cleanup_error}") from error
        if isinstance(error, ValueError):
            raise ValueError(f"{message}：{error}") from error
        raise RuntimeError(f"{message}：{error}") from error

    def __del__(self):
        try:
            self.abort()
        except Exception:
            pass
