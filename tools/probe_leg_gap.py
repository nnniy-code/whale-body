"""tools/probe_leg_gap.py — 查「腿的动作」有多少缺口。"""
import json
import sys

sys.path.insert(0, r"D:\DSproject\body-v2")

from core.predicates import PREDICATES


def main():
    fams = json.load(open(r"D:\DSproject\body-v2\config\seed_families.json",
                          encoding="utf-8"))["families"]
    print("=== 各族的步态参数 ===")
    print(f"{'族':<14} {'stride':>7} {'period':>7}  说明")
    for n, f in fams.items():
        G = f.get("G") or {}
        stride = float(G.get("stride", 0.0))
        period = float(G.get("period", 1.0))
        note = "有步态（腿动）" if abs(stride) > 1e-6 else "静态（腿不动）"
        print(f"{n:<14} {stride:>7.2f} {period:>7.2f}  {note}")

    print("\n=== 腿相关谓词 ===")
    leg = sorted(p for p in PREDICATES if any(k in p for k in ("foot", "leg", "knee")))
    for p in leg:
        print(" ", p)

    print("\n=== 缺口 ===")
    print("  · 蹲下 / 坐下 / 躺下：腿要弯，但 P 里没有腿的参数")
    print("  · 只有 foot_at_point 能指定脚的位置（通过 FootTarget，不走谓词）")
    print("  · 谓词没法说「膝盖弯到 90 度」——knee_angle 能查，但求解器搜不到")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
