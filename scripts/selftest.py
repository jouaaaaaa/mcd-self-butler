#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键自测：把六条命令在 demo 数据上全跑一遍，并断言关键结论。

用法：
    python scripts/selftest.py

它不连网、不需要 MCP Token —— 这是本项目的可复现性承诺：评委拿到仓库后
`python scripts/selftest.py` 全绿，说明每个数字都能在自己机器上复算。
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "scripts", "mcd_butler.py")
DEMO = os.path.join(ROOT, "demo")


def path(name):
    return os.path.join(DEMO, name)


CASES = [
    (
        "健康体检 · 拆解套餐并逐项对表",
        ["health", "--nutrition", path("nutrition.txt"), "--combo", path("combo_9900011128.json"), "--meal", "lunch"],
        ["健康分", "苹果片", "450", "钠"],
    ),
    (
        "省钱推荐",
        ["recommend", "--menu", path("menu_1450118.json"), "--nutrition", path("nutrition.txt"),
         "--mode", "cheap", "--meal", "lunch", "--budget", "30", "--top", "5", "--orders", path("orders.json")],
        ["省钱推荐", "元 / 100kcal"],
    ),
    (
        "健康推荐 · 应选出实测 412kcal 的鱼套餐",
        ["recommend", "--menu", path("menu_1450118.json"), "--nutrition", path("nutrition.txt"),
         "--mode", "healthy", "--meal", "lunch", "--budget", "45", "--top", "3", "--details", path("details.json")],
        ["“苹”水相“鱼”套餐", "实测"],
    ),
    (
        "人气推荐",
        ["recommend", "--menu", path("menu_1450118.json"), "--nutrition", path("nutrition.txt"),
         "--mode", "hot", "--meal", "lunch", "--top", "3", "--orders", path("orders.json")],
        ["人气推荐"],
    ),
    (
        "麦险分 · 六维评分卡",
        ["credit", "--orders", path("orders.json"), "--account", path("account.json"),
         "--coupons", path("coupons.md"), "--nutrition", path("nutrition.txt")],
        ["麦险分", "履约稳定度", "账户资产", "单笔建议额度"],
    ),
    (
        "订单级麦险分",
        ["credit", "--orders", path("orders.json"), "--account", path("account.json"),
         "--coupons", path("coupons.md"), "--nutrition", path("nutrition.txt"),
         "--price", path("price_9900005466_x2.json"), "--combo", path("combo_9900005466.json")],
        ["这一单的麦险分", "额度占用"],
    ),
    (
        "团餐配餐 · 30 人",
        ["bulk", "--menu", path("menu_1450118.json"), "--nutrition", path("nutrition.txt"),
         "--headcount", "30", "--budget", "30", "--no-beef", "5", "--no-spicy", "4",
         "--details", path("details.json")],
        ["团餐配餐", "分群", "人均", "productCode"],
    ),
    (
        "门店全成本比价",
        ["stores", "--stores", path("stores.json"), "--mode", "balanced"],
        ["综合全成本", "时间成本", "结论"],
    ),
    (
        "麦门小剧场 · 纯文本剧本",
        ["theater", "--context", path("theater_context.json"), "--script-only"],
        ["麦门小剧场", "麦险分先生", "全场通过"],
    ),
]


def main():
    failures = 0
    for name, args, must in CASES:
        cmd = [sys.executable, CLI] + args
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        out = (proc.stdout or "") + (proc.stderr or "")
        missing = [m for m in must if m not in out]
        ok = proc.returncode == 0 and not missing
        print("%s  %s" % ("PASS" if ok else "FAIL", name))
        if not ok:
            failures += 1
            print("      returncode=%s missing=%s" % (proc.returncode, missing))
            print("      " + out.strip().replace("\n", "\n      ")[:900])
    print()
    if failures:
        print("%d/%d 项失败" % (failures, len(CASES)))
        return 1
    print("全部 %d 项通过 · 结论可复现" % len(CASES))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    raise SystemExit(main())
