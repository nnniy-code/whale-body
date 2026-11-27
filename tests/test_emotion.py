"""tests/test_emotion.py — P1.5 验收。

怎么测：python tests/test_emotion.py
预期现象：[emotion] ALL PASS
调参位置：config/emotion_bias.json（k 与 tau）
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.emotion import (  # noqa: E402
    AXES, EmotionState, apply_bias, load_table, normalized, validate_table,
)

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


RANGES = {
    "lean": (-35, 35), "side_bend": (-25, 25),
    "head_nod": (-30, 30), "head_tilt": (-30, 30), "head_yaw": (-45, 45),
    "arm_swing": (0, 60), "arm_lift": (-20, 120), "elbow_bend": (0, 140),
    "hand_bias": (-30, 30), "tail_curl": (-40, 40),
    "period": (0.4, 1.5), "foot_lift": (0, 0.18), "stance_width": (0.05, 0.35),
    "turn_rate": (-180, 180), "duty": (0.3, 0.95), "stride": (-0.6, 0.6),
    "targets": (0, 1),
}

PARAMS = {"lean": 0.0, "head_yaw": 0.0, "arm_swing": 20.0, "tail_curl": 0.0,
          "period": 1.0, "stance_width": 0.16, "duty": 0.62, "stride": 0.0}


def main() -> int:
    table = load_table()

    print("[1] 配置合法性")
    bad = validate_table(table)
    check("forbidden 没出现在 axes 里", bad == [], str(bad))
    check("k_max = 0.15", abs(table["global_k_max"] - 0.15) < 1e-9)
    check("四轴齐全", all(a in table["axes"] for a in AXES))

    print("[2] 低通：1 秒时间常数")
    st = EmotionState(tau_s=1.0, init={a: 50 for a in AXES})
    st.set_raw({"val": 100})
    v = st.update(1.0)
    check("1s 后达到 ~63%", abs(v["val"] - (50 + 0.632 * 50)) < 1.5, f"{v['val']:.2f}")
    for _ in range(120):
        st.update(1 / 30)
    check("4s 后接近 100", st.snapshot()["val"] > 95, f"{st.snapshot()['val']:.2f}")

    print("[3] dt 无关性（30fps vs 60fps 结果一致）")
    a = EmotionState(tau_s=1.0, init={x: 50 for x in AXES})
    b = EmotionState(tau_s=1.0, init={x: 50 for x in AXES})
    for s in (a, b):
        s.set_raw({"val": 100})
    for _ in range(30):
        a.update(1 / 30)
    for _ in range(60):
        b.update(1 / 60)
    check("两者差 <0.01", abs(a.snapshot()["val"] - b.snapshot()["val"]) < 0.01,
          f"{a.snapshot()['val']:.4f} vs {b.snapshot()['val']:.4f}")

    print("[4] 偏置有界（±15%）")
    axes_hi = {a: 100 for a in AXES}
    out = apply_bias(PARAMS, axes_hi, table, RANGES)
    for p in ("lean", "head_yaw", "arm_swing"):
        lo, hi = RANGES[p]
        limit = (hi - lo) / 2 * 0.15
        delta = abs(out[p] - PARAMS[p])
        check(f"{p} 偏移 ≤ {limit:.3f}", delta <= limit + 1e-9, f"delta={delta:.4f}")

    print("[5] k=0 时完全不变")
    zero_k = {"axes": {a: {} for a in AXES}, "forbidden": [], "global_k_max": 0.15}
    out0 = apply_bias(PARAMS, axes_hi, zero_k, RANGES)
    check("参数逐位相同", out0 == PARAMS)

    print("[6] forbidden 生效（targets 不被推漂）")
    evil = {"axes": {"val": {"targets": 0.5}}, "forbidden": ["targets"], "global_k_max": 0.15}
    try:
        apply_bias(PARAMS, axes_hi, evil, RANGES)
        check("非法配置被拒绝", False, "没有抛异常")
    except ValueError:
        check("非法配置被拒绝", True)

    print("[7] 中立情绪（全 50）不产生偏移")
    neutral = {a: 50 for a in AXES}
    outn = apply_bias(PARAMS, neutral, table, RANGES)
    check("全轴 50 → 无偏移", all(abs(outn[k] - PARAMS[k]) < 1e-12 for k in PARAMS))

    print("[8] 方向正确（val 高 → 更前倾、尾巴更卷）")
    outv = apply_bias(PARAMS, {"val": 100, "aro": 50, "close": 50, "guard": 50}, table, RANGES)
    check("lean 增大", outv["lean"] > PARAMS["lean"], f"{outv['lean']:.3f}")
    check("tail_curl 增大", outv["tail_curl"] > PARAMS["tail_curl"], f"{outv['tail_curl']:.3f}")
    check("period 变小（走得轻快）", outv["period"] < PARAMS["period"], f"{outv['period']:.3f}")

    print("[9] duty / stride 不受情绪影响")
    check("duty 不变", abs(outv["duty"] - PARAMS["duty"]) < 1e-12)
    check("stride 不变", abs(outv["stride"] - PARAMS["stride"]) < 1e-12)

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
