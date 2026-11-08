"""无界面 CLI：读取 → 处理链 → 导出（供批量处理/脚本化使用）。

用法：
    python -m apps.cli data/scene.pcd --info
    python -m apps.cli data/cloud.pcd --stage voxel:0.2 --stage sor:20,2.0 --export out.ply
    python -m apps.cli data/ --outdir out --stage voxel:0.5
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.io.readers import ReadOptions, read_cloud
from core.io.scanner import collect_files
from core.commands import Command, commands_from_json, commands_to_json, get_spec, specs
from core.pipeline import parse_stage, run_pipeline


def build_parser():
    parser = argparse.ArgumentParser(
        prog="pointlab", description="PointLabStudio 无界面处理工具（读取 → 处理链 → 导出）")
    parser.add_argument("input", nargs="?", help="点云文件或目录")
    parser.add_argument("--stage", action="append", default=[], metavar="OP:ARGS",
                        help="处理步骤，可重复并按顺序执行，例如 voxel:0.2 / sor:20,2.0 / crop:-1,1,-1,1,-1,1")
    parser.add_argument("--commands", metavar="FILE",
                        help="命令列表 JSON（UI 历史 / 处理链导出），与 --stage 二选一")
    parser.add_argument("--print-commands", action="store_true",
                        help="只打印解析后的命令 JSON，便于脚本复用")
    parser.add_argument("--list-commands", action="store_true",
                        help="列出全部处理命令及参数后退出")
    parser.add_argument("--export", metavar="PATH", help="单个输入时的导出文件（.ply/.pcd/.xyz）")
    parser.add_argument("--outdir", metavar="DIR", help="目录输入时的导出目录（默认输出 .ply）")
    parser.add_argument("--info", action="store_true", help="打印点云统计信息")
    parser.add_argument("--max-points", type=int, default=0, help="读取后先下采样到指定点数")
    parser.add_argument("--bin-dim", default="auto", help=".bin 维度（默认 auto）")
    parser.add_argument("--dtype", default="float32")
    parser.add_argument("--npy-key", default=None)
    return parser


def write_cloud(path: str, entity) -> None:
    from core.io.exporters import export_entity

    export_entity(entity, path)


def _progress_printer(label: str):
    last = {"pct": -10}

    def report(fraction: float):
        pct = int(fraction * 100)
        if pct >= last["pct"] + 10 or pct == 100:
            last["pct"] = pct
            print(f"  {label} {pct}%", file=sys.stderr)

    return report


def list_commands() -> None:
    for spec in specs().values():
        params = "，".join(f"{p.key}={p.default!r}" for p in spec.params) or "无参数"
        print(f"{spec.op:<8} {spec.label:<10} {params}")
        if spec.hint:
            print(f"         {spec.hint}")


def resolve_steps(args) -> list:
    """把 --stage / --commands 统一解析成命令列表，并提前校验参数。"""
    if args.stage and args.commands:
        raise ValueError("--stage 与 --commands 不能同时使用")
    if args.commands:
        try:
            with open(args.commands, encoding="utf-8") as fh:
                commands = commands_from_json(fh.read())
        except OSError as exc:
            raise ValueError(f"读取命令文件失败: {exc}") from exc
    else:
        commands = [Command.from_step(parse_stage(text)) for text in args.stage]
    for command in commands:
        get_spec(command.op).normalize(command.params)
    return [c.to_dict() for c in commands]


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list_commands:
        list_commands()
        return 0
    if not args.input:
        parser.error("需要输入文件或目录（或用 --list-commands 查看命令）")
    options = ReadOptions(bin_dim=args.bin_dim, dtype=args.dtype,
                          npy_key=args.npy_key, max_points=args.max_points)

    if os.path.isdir(args.input):
        files = collect_files(args.input)
        if args.outdir:
            output_root = os.path.normcase(os.path.abspath(args.outdir))
            if output_root == os.path.normcase(os.path.abspath(args.input)):
                print("输出目录不能与输入目录相同", file=sys.stderr)
                return 1
            files = [p for p in files if not os.path.normcase(os.path.abspath(p)).startswith(output_root + os.sep)]
    elif os.path.isfile(args.input):
        files = [args.input]
    else:
        print(f"路径不存在: {args.input}", file=sys.stderr)
        return 1
    if not files:
        print("没有找到支持的点云文件", file=sys.stderr)
        return 1

    if args.export and (len(files) != 1 or args.outdir):
        print("--export 需要单个输入，且不能与 --outdir 同时使用", file=sys.stderr)
        return 1

    try:
        steps = resolve_steps(args)
    except ValueError as exc:
        print(f"参数错误: {exc}", file=sys.stderr)
        return 1
    if args.print_commands:
        print(commands_to_json([Command.from_step(step) for step in steps]))
        return 0

    exported = 0
    failed = 0
    output_names = {}
    relative_names = {p: os.path.relpath(p, args.input) if os.path.isdir(args.input)
                      else os.path.basename(p) for p in files}
    for path in files:
        stem = os.path.splitext(relative_names[path])[0].casefold()
        output_names[stem] = output_names.get(stem, 0) + 1
    for path in files:
        try:
            entity = read_cloud(path, options)
            print(f"{os.path.basename(path)}: {entity.point_count:,} 点", file=sys.stderr)
            if steps:
                entity = run_pipeline(entity, steps, progress=_progress_printer("处理"))
                entity.name = f"{os.path.splitext(os.path.basename(path))[0]} · 处理后"
                print(f"  处理完成: {entity.point_count:,} 点", file=sys.stderr)

            if args.info or not (args.export or args.outdir):
                mn, mx = entity.bounds()
                size = mx - mn
                print(f"  {entity.name}: {entity.point_count:,} 点")
                print(f"  范围: ({mn[0]:.2f}, {mn[1]:.2f}, {mn[2]:.2f}) ~ "
                      f"({mx[0]:.2f}, {mx[1]:.2f}, {mx[2]:.2f})")
                print(f"  尺寸: {size[0]:.2f} × {size[1]:.2f} × {size[2]:.2f}")

            if args.export and len(files) == 1:
                parent = os.path.dirname(os.path.abspath(args.export))
                os.makedirs(parent, exist_ok=True)
                write_cloud(args.export, entity)
                exported += 1
                print(f"  已导出: {args.export}", file=sys.stderr)
            elif args.outdir:
                os.makedirs(args.outdir, exist_ok=True)
                stem = os.path.splitext(relative_names[path])[0]
                name = relative_names[path] if output_names[stem.casefold()] > 1 else stem
                target = os.path.join(args.outdir, name + ".ply")
                os.makedirs(os.path.dirname(target), exist_ok=True)
                write_cloud(target, entity)
                exported += 1
                print(f"  已导出: {target}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"处理失败: {os.path.basename(path)} — {exc}", file=sys.stderr)

    print(f"完成：{len(files)} 个输入，导出 {exported} 个", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
