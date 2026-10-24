#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦当劳自助管家 · 命令行入口。

六条命令，对应六个能力：
    recommend  每日省钱 / 健康 / 人气推荐
    health     套餐健康度体检（热量、蛋白、脂肪、钠、钙）
    credit     麦险分：「信贷评分卡 × 麦当劳」
    bulk       团餐批量智能配餐
    stores     门店全成本比价（最近 / 最便宜 / 综合）
    theater    麦门小剧场（等待时看五个小人吵架）

设计原则：**只在本地做计算，不直接调 MCP**。
MCP 调用由 Agent（WorkBuddy）负责，原始响应落盘成 JSON 后喂给本脚本。
好处是：可离线复现、可单测、评审不需要 token 也能把每个数字跑一遍。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import bulk as bulk_mod          # noqa: E402
from lib import credit as credit_mod      # noqa: E402
from lib import health as health_mod      # noqa: E402
from lib import mcdata                    # noqa: E402
from lib import recommend as rec_mod      # noqa: E402
from lib import theater as theater_mod    # noqa: E402


# --------------------------------------------------------------------------- #
# 输出小工具
# --------------------------------------------------------------------------- #


def money(v):
    try:
        return "¥%.1f" % float(v)
    except (TypeError, ValueError):
        return "—"


def bar(value, width=16, char="█", empty="·"):
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 0
    filled = int(round(max(0.0, min(100.0, value)) / 100 * width))
    return char * filled + empty * (width - filled)


def table(headers, rows, aligns=None):
    aligns = aligns or ["l"] * len(headers)
    cols = [str(h) for h in headers]
    data = [[("" if c is None else str(c)) for c in r] for r in rows]

    def w(text):
        # 中文按 2 字宽对齐，表格才不会歪
        return sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)

    widths = [w(c) for c in cols]
    for r in data:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], w(c))
    out = []
    out.append("| " + " | ".join(_pad(c, widths[i], aligns[i]) for i, c in enumerate(cols)) + " |")
    out.append("|" + "|".join("-" * (widths[i] + 2) for i in range(len(cols))) + "|")
    for r in data:
        out.append("| " + " | ".join(_pad(c, widths[i], aligns[i]) for i, c in enumerate(r)) + " |")
    return "\n".join(out)


def _pad(text, width, align):
    gap = width - sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)
    gap = max(0, gap)
    if align == "r":
        return " " * gap + text
    if align == "c":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap


# --------------------------------------------------------------------------- #
# recommend
# --------------------------------------------------------------------------- #


def render_recommend(result):
    if not result["items"]:
        return "没找到符合条件的餐品。%s" % result.get("note", "")

    mode_name = {"cheap": "省钱", "healthy": "健康", "hot": "人气"}[result["mode"]]
    lines = [
        "## %s · %s推荐" % (result.get("store") or "本店", mode_name),
        "",
        "> 候选 %d 个，取前 %d。价格为门店现价，健康数据标注「实测」= 已用 query-meal-detail 拆解后逐项对表。"
        % (result["total_candidates"], len(result["items"])),
        "",
    ]
    rows = []
    for i in result["items"]:
        data_tag = ("%d kcal" % i["kcal"]) if i["kcal"] else "—"
        if i["confidence"] == "estimate":
            data_tag += "(估)"
        rows.append(
            [
                i["rank"],
                i["name"],
                money(i["price"]),
                money(i["originalPrice"]),
                ("%.0f%%" % (i["value"] * 100)) if i["value"] else "—",
                data_tag,
                i["health"] if i["health"] is not None else "—",
                ("%.1f" % i["rank_score"]),
            ]
        )
    lines.append(table(["#", "餐品", "现价", "原价", "省", "热量", "健康分", "综合"], rows,
                       ["c", "l", "r", "r", "r", "r", "r", "r"]))
    lines.append("")
    lines.append("**为什么是它**")
    for i in result["items"][:3]:
        lines.append("- **%s**：%s" % (i["name"], i["reason"]))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# health
# --------------------------------------------------------------------------- #


def render_health(h):
    title = h.get("title") or "本单"
    rows = []
    for r in h["rows"]:
        rows.append(
            [
                r["name"],
                r["quantity"],
                ("%d" % r["kcal"]) if r.get("kcal") is not None else "无数据",
                ("%.1f" % r["protein"]) if r.get("protein") is not None else "—",
                ("%.1f" % r["fat"]) if r.get("fat") is not None else "—",
                ("%.0f" % r["sodium"]) if r.get("sodium") is not None else "—",
                ("%.0f" % r["calcium"]) if r.get("calcium") is not None else "—",
            ]
        )
    t = h["totals"]
    rows.append(["**合计**", "", "**%d**" % t["kcal"], "**%.1f**" % t["protein"], "**%.1f**" % t["fat"],
                 "**%.0f**" % t["sodium"], "**%.0f**" % t["calcium"]])

    lines = [
        "## 健康体检 · %s" % title,
        "",
    ]
    if h["score"] is None:
        lines.append("**健康分 —**　官方营养表不覆盖这些餐品，本次不给分（不编数字）。")
    else:
        lines.append(
            "**健康分 %d / 100（%s · %s）**  %s"
            % (h["score"], h["grade"], health_mod.GRADE_TITLE.get(h["grade"].rstrip("?"), ""), bar(h["score"]))
        )
    lines += [
        "",
        table(["组成", "份数", "kcal", "蛋白 g", "脂肪 g", "钠 mg", "钙 mg"], rows,
              ["l", "c", "r", "r", "r", "r", "r"]),
        "",
        "**占每日参考值**（2000 kcal / 钠 2000mg / 蛋白 60g / 脂肪 60g / 钙 800mg）",
        "",
    ]
    for key, label in (("kcal", "能量"), ("protein", "蛋白质"), ("fat", "脂肪"), ("sodium", "钠"), ("calcium", "钙")):
        lines.append("- %s：%.0f%%  %s" % (label, h["daily_ratio"][key], bar(h["daily_ratio"][key])))

    lines.append("")
    lines.append("**扣分项**")
    for name, pen, note in h["penalties"]:
        lines.append("- %s `-%g` %s" % (name, pen, note))
    if h["bonus"]:
        lines.append("- 蔬果加分 `+%g`" % h["bonus"])

    if h["flags"]:
        lines.append("")
        lines.append("**标签**：" + "  ".join("`%s`" % f for f in h["flags"]))

    lines.append("")
    lines.append("**怎么改**")
    for a in h["advice"]:
        lines.append("- " + a)

    if h["missing"]:
        lines.append("")
        lines.append("_以下子项在官方营养表里没有对应条目，未计入合计：%s（覆盖率 %.0f%%）_"
                     % ("、".join(h["missing"]), h["coverage"]))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# credit
# --------------------------------------------------------------------------- #


def render_credit(c, order=None):
    lines = [
        "## 麦险分 · %d 分" % c["score"],
        "",
        "**%s**  ·  %s" % (c["tier"]["name"], c["tier"]["comment"]),
        "",
        "`%s` %d / 950　单笔建议额度 **%s**　履约风险约 **%.1f%%**"
        % (bar(c["score"] / 950 * 100, 24), c["score"], money(c["tier"]["limit"]), c["risk"]),
        "",
        table(
            ["维度", "权重", "得分", "", "依据"],
            [[d["name"], "%.0f%%" % (d["weight"] * 100), "%.0f" % d["score"], bar(d["score"], 12), d["detail"]]
             for d in c["dimensions"]],
            ["l", "r", "r", "l", "l"],
        ),
        "",
    ]
    if c.get("orders_count") == 0:
        lines.append("> 没有历史订单，本次按冷启动规则打分（履约 40 / 节律 45 / 预算 45）。")
        lines.append("")
    lines.append("**加分项**：" + "、".join(c["strong"]))
    lines.append("**拖后腿**：" + "、".join(c["weak"]))
    lines.append("")
    lines.append("**提分建议**")
    for t in c["tips"]:
        lines.append("- " + t)

    if order:
        lines.append("")
        lines.append("### 这一单的麦险分：%d（%s）" % (order["score"], order["grade"]))
        lines.append("")
        lines.append("- 额度占用：%.1f%%（%s）" % (order["usage"], order["usage_note"]))
        lines.append("- %s" % order.get("health_note", "健康分 %s" % order["health"]))
        lines.append("- 风控结论：%s" % order["note"])
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# bulk
# --------------------------------------------------------------------------- #


def render_bulk(b):
    if not b.get("ok"):
        return "## 团餐配餐\n\n%s" % b.get("note", "无解")

    lines = [
        "## 团餐配餐 · %d 人 × %s" % (b["headcount"], money(b["budgetPerPerson"])),
        "",
        "分群：" + "、".join("%s %d 人" % (g["label"], g["count"]) for g in b["groups"]),
        "",
    ]
    rows = []
    for p in b["picks"]:
        rows.append(
            [
                p["name"],
                p["group"],
                p["quantity"],
                money(p["unitPrice"]),
                money(p["subtotal"]),
                "%d" % p["kcal"],
                "%d%s" % (p["health"], "" if p["measured"] else "(估)"),
            ]
        )
    lines.append(table(["套餐", "分群", "份数", "单价", "小计", "kcal", "健康"], rows,
                       ["l", "l", "c", "r", "r", "r", "r"]))
    lines.append("")
    lines.append(
        "**合计 %s**（原价 %s，省 %s）　人均 **%s**　人均 %d kcal　共 %d 个不同套餐"
        % (money(b["totalCost"]), money(b["totalOriginal"]), money(b["saved"]),
           money(b["avgPerPerson"]), b["avgKcalPerPerson"], b["distinctItems"])
    )
    if b["warnings"]:
        lines.append("")
        for w in b["warnings"]:
            lines.append("> ⚠️ " + w)

    lines.append("")
    lines.append("**直接喂给 calculate-price 的 items**")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(b["orderItems"], ensure_ascii=False, indent=2))
    lines.append("```")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# stores
# --------------------------------------------------------------------------- #


def render_stores(s):
    rows = []
    for r in s["stores"]:
        marks = []
        if r["isBestTotal"]:
            marks.append("综合最优")
        if r["isCheapestPrice"]:
            marks.append("单价最低")
        rows.append(
            [
                r["rank"],
                r["storeName"],
                "%dm" % r["distance"],
                money(r["price"]),
                "%dmin" % r["wait"] if r["wait"] else "—",
                money(r["distCost"]),
                money(r["timeCost"]),
                money(r["totalCost"]),
                "、".join(marks) or "—",
            ]
        )
    mode_name = {"nearest": "最近优先", "cheapest": "最便宜优先", "balanced": "综合全成本"}[s["mode"]]
    lines = [
        "## 门店比价 · %s" % mode_name,
        "",
        "> 全成本 = 商品价 + 距离成本（往返 %.1f 元/km）+ 时间成本（步行往返 + 排队，%.2f 元/分钟）"
        % (s["travelCostPerKm"], s["timeValue"]),
        "",
        table(["#", "门店", "距离", "商品价", "排队", "距离成本", "时间成本", "全成本", "备注"], rows,
              ["c", "l", "r", "r", "r", "r", "r", "r", "l"]),
        "",
    ]
    best = s["stores"][0]
    cheapest = next((r for r in s["stores"] if r["isCheapestPrice"]), None)
    note = ""
    if cheapest and cheapest["storeCode"] != best["storeCode"]:
        note = "「%s」商品价最低（%s），但算上路上和排队的时间后并不是最划算的那家。" % (
            cheapest["storeName"], money(cheapest["price"]))
    lines.append("**结论**：去「%s」，全成本 %s。%s" % (best["storeName"], money(best["totalCost"]), note))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _load_menu(args):
    payload = mcdata.load_json(args.menu)
    return mcdata.Menu(payload)


def cmd_recommend(args):
    menu = _load_menu(args)
    nutri = mcdata.parse_nutrition(mcdata.load_any(args.nutrition))
    details = mcdata.load_json(args.details) if args.details else {}
    orders = mcdata.parse_orders(mcdata.load_json(args.orders)) if args.orders else []
    exclude = [x.strip() for x in (args.exclude or "").split(",") if x.strip()]
    result = rec_mod.recommend(
        menu, nutri, mode=args.mode, band=args.meal, budget=args.budget,
        top=args.top, details=details, orders=orders, exclude=exclude,
    )
    return result, render_recommend(result)


def cmd_health(args):
    nutri = mcdata.parse_nutrition(mcdata.load_any(args.nutrition))
    if args.combo:
        detail = mcdata.parse_detail(mcdata.load_json(args.combo))
        result = health_mod.analyze_combo(detail, nutri, band=args.meal)
    elif args.items:
        items = mcdata.parse_qty_items(args.items)
        result = health_mod.analyze(items, nutri, band=args.meal, title=args.title or "")
    else:
        raise SystemExit("health 需要 --combo 或 --items")
    return result, render_health(result)


def cmd_credit(args):
    orders = mcdata.parse_orders(mcdata.load_json(args.orders)) if args.orders else []
    account = mcdata.parse_account(mcdata.load_json(args.account)) if args.account else {"available": 0, "accumulated": 0}
    coupons = mcdata.parse_coupons(mcdata.load_any(args.coupons)) if args.coupons else []
    nutri = mcdata.parse_nutrition(mcdata.load_any(args.nutrition)) if args.nutrition else None

    result = credit_mod.score_account(orders, account, coupons, nutri=nutri, band=args.meal)
    order_result = None
    if args.price:
        payload = mcdata.load_any(args.price)
        data = mcdata.unwrap(payload)
        price_yuan = (data.get("price") or 0) / 100.0
        h = None
        if args.items:
            h = health_mod.analyze(mcdata.parse_qty_items(args.items), nutri, band=args.meal)
        elif args.combo:
            h = health_mod.analyze_combo(mcdata.parse_detail(mcdata.load_json(args.combo)), nutri, band=args.meal)
        if h:
            order_result = credit_mod.score_order(price_yuan, h, result)
            order_result["price"] = price_yuan
    if order_result:
        result["currentOrder"] = order_result
    return result, render_credit(result, order_result)


def cmd_bulk(args):
    menu = _load_menu(args)
    nutri = mcdata.parse_nutrition(mcdata.load_any(args.nutrition))
    details = mcdata.load_json(args.details) if args.details else {}
    result = bulk_mod.plan(
        menu, nutri, headcount=args.headcount, budget=args.budget, band=args.meal,
        no_beef=args.no_beef, no_spicy=args.no_spicy,
        healthy_ratio=args.healthy_ratio, spicy_ratio=args.spicy_ratio,
        details=details, allow_share=not args.no_share,
    )
    return result, render_bulk(result)


def cmd_stores(args):
    payload = mcdata.load_any(args.stores)
    stores = mcdata.parse_stores(payload) if isinstance(mcdata.unwrap(payload), (dict, list)) else payload
    # 精简格式（带 price / waitMinutes）需要原样带上
    if isinstance(payload, dict) and "stores" in payload:
        stores = payload["stores"]
    result = rec_mod.rank_stores(stores, mode=args.mode)
    return result, render_stores(result)


def cmd_theater(args):
    ctx = mcdata.load_any(args.context) if args.context else {}
    if args.script_only:
        script = theater_mod.build_script(ctx)
        return {"script": script}, theater_mod.to_text(script, ctx)
    ctx["_script"] = theater_mod.build_script(ctx)
    html_text = theater_mod.render_html(ctx)
    out_path = os.path.abspath(args.out or "theater.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_text)
    text = theater_mod.to_text(ctx["_script"], ctx)
    return {"out": out_path, "script": ctx["_script"]}, text + "\n\n_小剧场已生成：%s_" % out_path


def build_parser():
    p = argparse.ArgumentParser(
        prog="mcd_butler",
        description="麦当劳自助管家 · 本地计算引擎（数据来自麦当劳 MCP）",
    )
    p.add_argument("--json", action="store_true", default=False,
                   help="输出原始 JSON 而不是 Markdown")
    sub = p.add_subparsers(dest="cmd", required=True)
    # 子命令也接受 --json（放在子命令后面的写法更自然），用 SUPPRESS 避免覆盖前面的值
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="输出原始 JSON 而不是 Markdown")

    r = sub.add_parser("recommend", parents=[common], help="每日推荐")
    r.add_argument("--menu", required=True)
    r.add_argument("--nutrition", required=True)
    r.add_argument("--details", help="套餐详情 JSON：{code: query-meal-detail 响应}")
    r.add_argument("--orders", help="历史订单 JSON，用于「常点」加权")
    r.add_argument("--mode", choices=["cheap", "healthy", "hot"], default="cheap")
    r.add_argument("--meal", choices=list(health_mod.MEAL_BANDS), default="lunch")
    r.add_argument("--budget", type=float)
    r.add_argument("--top", type=int, default=5)
    r.add_argument("--exclude", help="排除关键词，逗号分隔，如 牛,辣")
    r.set_defaults(func=cmd_recommend)

    h = sub.add_parser("health", parents=[common], help="健康度分析")
    h.add_argument("--nutrition", required=True)
    h.add_argument("--combo", help="query-meal-detail 响应文件")
    h.add_argument("--items", help='直接给餐品，如 "巨无霸*1,中薯条*1"')
    h.add_argument("--meal", choices=list(health_mod.MEAL_BANDS), default="lunch")
    h.add_argument("--title")
    h.set_defaults(func=cmd_health)

    c = sub.add_parser("credit", parents=[common], help="麦险分")
    c.add_argument("--orders")
    c.add_argument("--account")
    c.add_argument("--coupons")
    c.add_argument("--nutrition")
    c.add_argument("--price", help="calculate-price 响应，用于给当前这一单评分")
    c.add_argument("--items")
    c.add_argument("--combo")
    c.add_argument("--meal", choices=list(health_mod.MEAL_BANDS), default="lunch")
    c.set_defaults(func=cmd_credit)

    b = sub.add_parser("bulk", parents=[common], help="团餐批量配餐")
    b.add_argument("--menu", required=True)
    b.add_argument("--nutrition", required=True)
    b.add_argument("--headcount", type=int, required=True)
    b.add_argument("--budget", type=float, required=True)
    b.add_argument("--no-beef", type=int, default=0, dest="no_beef")
    b.add_argument("--no-spicy", type=int, default=0, dest="no_spicy")
    b.add_argument("--healthy-ratio", type=float, default=0.3, dest="healthy_ratio")
    b.add_argument("--spicy-ratio", type=float, default=0.3, dest="spicy_ratio")
    b.add_argument("--details")
    b.add_argument("--meal", choices=list(health_mod.MEAL_BANDS), default="lunch")
    b.add_argument("--no-share", action="store_true")
    b.set_defaults(func=cmd_bulk)

    s = sub.add_parser("stores", parents=[common], help="门店比价")
    s.add_argument("--stores", required=True)
    s.add_argument("--mode", choices=["nearest", "cheapest", "balanced"], default="balanced")
    s.set_defaults(func=cmd_stores)

    t = sub.add_parser("theater", parents=[common], help="麦门小剧场")
    t.add_argument("--context")
    t.add_argument("--out", default="theater.html")
    t.add_argument("--script-only", action="store_true")
    t.set_defaults(func=cmd_theater)
    return p


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    args = build_parser().parse_args(argv)
    result, markdown = args.func(args)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(markdown)


if __name__ == "__main__":
    main()
