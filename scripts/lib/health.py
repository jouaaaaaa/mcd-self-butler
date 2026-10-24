# -*- coding: utf-8 -*-
"""健康度引擎。

输入「一餐吃了什么」，输出「这一餐在营养上学不学得动」。
评分模型见 references/model-spec.md，这里是唯一实现，文档与代码一一对应。

核心思路：麦当劳没有「套餐营养表」，只有单品的营养表。所以必须先通过
`query-meal-detail` 把套餐拆成子项，再逐项映射到营养库求和 —— 这是本 Skill
能给出可信热量数字的根本原因，也是它比「直接把套餐名丢给模型猜」靠谱的地方。
"""

from __future__ import annotations

from . import mcdata

# --------------------------------------------------------------------------- #
# 参考区间
# --------------------------------------------------------------------------- #

#: 不同餐段的合理能量区间（kcal）。依据：成年轻体力活动人群每日 2000 kcal，
#: 三餐按 3:4:3 分配，正餐 600-800、早餐 400-500、下午茶 200-400。
MEAL_BANDS = {
    "breakfast": (350, 550),
    "lunch": (500, 750),
    "dinner": (500, 750),
    "tea": (180, 400),
    "night": (250, 550),
}

#: 每日参考摄入量（用于占比展示）
DAILY_REFS = {
    "kcal": 2000.0,
    "protein": 60.0,
    "fat": 60.0,        # 脂肪供能比 27%，对应约 60g
    "carb": 300.0,
    "sodium": 2000.0,   # WHO 建议成人每日钠 < 2000mg
    "calcium": 800.0,
}

# --------------------------------------------------------------------------- #
# 角色识别
# --------------------------------------------------------------------------- #

FRUIT_VEG = ("苹果", "玉米", "沙拉", "鲜蔬", "蔬菜", "番茄", "生菜", "橙", "蕉", "笋")
FRIED = ("薯条", "薯饼", "脆薯", "鸡翅", "麦乐鸡", "鸡排", "脆汁鸡", "鸡球", "油条", "鸡块", "鸡米花", "炸")
DESSERT = ("冰淇淋", "新地", "麦旋风", "派", "圆筒", "阿芙佳朵", "拉明顿", "奶冻", "爆珠", "麦旋酷", "蛋糕")
SUGARY = ("可乐", "雪碧", "果汁", "橙", "朱古力", "奶昔", "冰糖", "奶茶", "焦糖", "玛奇朵")
SUGAR_FREE = ("无糖", "零度", "纯悦", "美式", "鲜萃", "浓缩", "锡兰", "伯爵", "清水", "矿泉水", "苏打")
BURGER = ("堡", "麦满分", "卷", "三明治")

#: 高钠主体的替换建议（数字取自官方营养表，可复核）
SWAPS = {
    "巨无霸": ("麦香鱼", "钠 961→556mg，能量 513→325kcal"),
    "双层吉士汉堡": ("吉士汉堡包", "钠 1017→673mg，能量 429→294kcal"),
    "不素之霸双层牛堡": ("吉士汉堡包", "钠 1012→673mg，能量 493→294kcal"),
    "培根安格斯厚牛堡": ("芝士安格斯厚牛堡", "钠 1037→897mg，能量 707→696kcal"),
    "培根蔬萃双层牛堡": ("吉士汉堡包", "钠 728→673mg，能量 442→294kcal"),
    "麦辣鸡腿汉堡": ("板烧鸡腿堡", "能量 485→391kcal，脂肪 24→17g"),
    "麦辣鸡腿堡": ("板烧鸡腿堡", "能量 485→391kcal，脂肪 24→17g"),
    "双层深海鳕鱼堡": ("麦香鱼", "钠 917→556mg，能量 485→325kcal"),
    "大薯条": ("中薯条", "能量 379→289kcal，脂肪 16→12g"),
    "中薯条": ("小薯条", "能量 289→210kcal，脂肪 12→9g"),
    "可乐中杯": ("无糖可口可乐中杯", "能量 147→0kcal"),
    "可乐大杯": ("无糖可口可乐大杯", "能量 224→0kcal"),
    "雪碧中杯": ("无糖可口可乐中杯", "能量 105→0kcal"),
    "原味板烧鸡腿麦满分": ("火腿扒麦满分", "能量 246→261kcal，脂肪 9→12g（钠 611→608mg 持平）"),
    "猪柳蛋麦满分": ("火腿扒麦满分", "钠 846→608mg，能量 387→261kcal"),
    "脆薯饼": ("苹果片", "能量 146→32kcal，钠 311→0mg"),
}


def classify(name: str):
    """给单个餐品打角色标签，用于糖油负担与蔬果加分。"""
    tags = set()
    if any(k in name for k in FRUIT_VEG):
        tags.add("veg")
    if any(k in name for k in FRIED):
        tags.add("fried")
    if any(k in name for k in DESSERT):
        tags.add("dessert")
    if any(k in name for k in SUGAR_FREE):
        tags.add("sugar_free")
    elif any(k in name for k in SUGARY):
        tags.add("sugary")
    if any(k in name for k in BURGER):
        tags.add("burger")
    if not tags:
        tags.add("other")
    return tags


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #


def analyze(items, nutri: mcdata.NutriDB, band: str = "lunch", title: str = ""):
    """分析一餐。

    items: [{"name": "巨无霸", "quantity": 1}, ...]
    返回结构化结果，包含逐项营养、汇总、评分、扣分项、建议。
    """
    band_key = band if band in MEAL_BANDS else "lunch"
    lo, hi = MEAL_BANDS[band_key]

    rows = []
    totals = {f: 0.0 for f in ("kcal", "protein", "fat", "carb", "sodium", "calcium")}
    matched, missing = 0, []
    role_counter = {"fried": 0, "dessert": 0, "sugary": 0, "veg": 0}

    for it in items:
        name = it["name"]
        qty = int(it.get("quantity") or 1)
        entry, conf = nutri.lookup(name)
        roles = classify(name)
        if entry:
            matched += 1
            row = {"name": name, "quantity": qty, "confidence": conf}
            for f in totals:
                row[f] = round(entry[f] * qty, 1)
                totals[f] += entry[f] * qty
        else:
            missing.append(name)
            row = {"name": name, "quantity": qty, "confidence": 0.0}
            for f in totals:
                row[f] = None
        if "veg" in roles:
            role_counter["veg"] += qty
        if "fried" in roles:
            role_counter["fried"] += qty
        if "dessert" in roles:
            role_counter["dessert"] += qty
        if "sugary" in roles:
            role_counter["sugary"] += qty
        rows.append(row)

    for f in totals:
        totals[f] = round(totals[f], 1)

    score, penalties, bonus = _score(totals, role_counter, lo, hi)
    flags = _flags(totals, role_counter, lo, hi)
    advice = _advice(totals, role_counter, rows, lo, hi, band_key)

    coverage = round(matched / len(items) * 100, 0) if items else 0.0
    # 没有一条命中营养表时，绝不能拿「全 0」算出一个看起来很真的分数
    if matched == 0:
        score, grade = None, "—"
        penalties, bonus = [], 0.0
        advice = [
            "这些餐品在官方营养表里没有对应条目（多为套餐名或已下架 SKU）。"
            "先用 query-meal-detail 拆成子项再分析，或换成营养表覆盖的单品。"
        ]
        flags.append("无营养数据")
    elif coverage < 60:
        grade = grade_of(score) + "?"
        flags.append("数据不全·仅供参考")
    else:
        grade = grade_of(score)

    return {
        "title": title,
        "band": band_key,
        "band_range": [lo, hi],
        "rows": rows,
        "totals": totals,
        "daily_ratio": {k: round(totals[k] / DAILY_REFS[k] * 100, 1) for k in totals},
        "score": score,
        "grade": grade,
        "penalties": penalties,
        "bonus": bonus,
        "roles": role_counter,
        "flags": flags,
        "advice": advice,
        "coverage": coverage,
        "missing": missing,
    }


def _score(totals, roles, lo, hi):
    """100 分制扣分模型。扣分明细可逐条复核。"""
    kcal = totals["kcal"] or 0.0
    protein = totals["protein"] or 0.0
    fat = totals["fat"] or 0.0
    sodium = totals["sodium"] or 0.0
    penalties = []

    # 1) 能量落在餐段合理区间（上限 30 分）
    if kcal < lo:
        p = min(30.0, (lo - kcal) / 20.0)
        penalties.append(("能量偏低", round(p, 1), "低于 %d kcal，容易两小时后又饿" % lo))
    elif kcal > hi:
        p = min(30.0, (kcal - hi) / 25.0)
        penalties.append(("能量偏高", round(p, 1), "超出 %d kcal，超出部分约需慢跑 %d 分钟" % (hi, int((kcal - hi) / 10))))
    else:
        penalties.append(("能量区间", 0.0, "落在 %d-%d kcal 的舒适区" % (lo, hi)))

    # 2) 蛋白质（上限 20 分）
    if protein >= 30:
        pp, note = 0.0, "蛋白质 %.0fg，很扎实" % protein
    elif protein >= 25:
        pp, note = 4.0, "蛋白质 %.0fg，达标" % protein
    elif protein >= 18:
        pp, note = 10.0, "蛋白质 %.0fg，勉强" % protein
    elif protein >= 12:
        pp, note = 16.0, "蛋白质 %.0fg，偏少" % protein
    else:
        pp, note = 20.0, "蛋白质 %.0fg，严重不足" % protein
    penalties.append(("蛋白质", pp, note))

    # 3) 脂肪供能比（上限 20 分）
    ratio = (fat * 9 / kcal) if kcal > 0 else 0.0
    if ratio <= 0.30:
        fp, note = 0.0, "脂肪供能比 %.0f%%，清爽" % (ratio * 100)
    elif ratio <= 0.35:
        fp, note = 5.0, "脂肪供能比 %.0f%%，尚可" % (ratio * 100)
    elif ratio <= 0.42:
        fp, note = 12.0, "脂肪供能比 %.0f%%，偏高" % (ratio * 100)
    else:
        fp, note = 20.0, "脂肪供能比 %.0f%%，油腻" % (ratio * 100)
    penalties.append(("脂肪结构", fp, note))

    # 4) 钠（上限 20 分）
    if sodium <= 800:
        sp, note = 0.0, "钠 %.0fmg，安全" % sodium
    elif sodium <= 1000:
        sp, note = 6.0, "钠 %.0fmg，略高" % sodium
    elif sodium <= 1300:
        sp, note = 12.0, "钠 %.0fmg，偏高" % sodium
    else:
        sp, note = 20.0, "钠 %.0fmg，超标（单餐建议 ≤800mg）" % sodium
    penalties.append(("钠", sp, note))

    # 5) 糖油负担（上限 16 分）
    load = min(16.0, 4.0 * roles["fried"] + (6.0 if roles["dessert"] else 0.0) + (5.0 if roles["sugary"] else 0.0))
    detail = []
    if roles["fried"]:
        detail.append("油炸 %d 项" % roles["fried"])
    if roles["dessert"]:
        detail.append("甜品 %d 项" % roles["dessert"])
    if roles["sugary"]:
        detail.append("含糖饮料 %d 项" % roles["sugary"])
    penalties.append(("糖油负担", load, "、".join(detail) if detail else "无油炸/甜品/含糖饮料"))

    # 6) 蔬果加分（上限 +10）
    bonus = min(10.0, 5.0 * roles["veg"])

    total_pen = sum(p for _, p, _ in penalties)
    score = max(0.0, min(100.0, 100.0 - total_pen + bonus))
    return int(round(score)), penalties, bonus


def _flags(totals, roles, lo, hi):
    flags = []
    if totals["sodium"] > 1000:
        flags.append("高钠")
    if totals["kcal"] > 0 and (totals["fat"] * 9 / totals["kcal"]) > 0.38:
        flags.append("高脂")
    if totals["kcal"] > hi:
        flags.append("高热量")
    if totals["protein"] < 18:
        flags.append("低蛋白")
    if roles["veg"] == 0:
        flags.append("无蔬果")
    if roles["fried"] >= 2:
        flags.append("多油炸")
    return flags


def _advice(totals, roles, rows, lo, hi, band):
    """生成可执行的建议：指名道姓说换哪一个，而不是「少吃点油」。"""
    advice = []
    kcal, protein, fat, sodium = totals["kcal"], totals["protein"], totals["fat"], totals["sodium"]

    if kcal > hi:
        advice.append(
            "能量 %d kcal，比 %s 建议上限高 %d kcal。优先砍掉最不扛饿的那一项：把「大薯条」换「中薯条」可省 90 kcal。"
            % (kcal, _band_name(band), kcal - hi)
        )
    if kcal < lo and kcal > 0:
        advice.append(
            "能量只有 %d kcal，撑不到下一餐。加一份「麦乐鸡5块」（213 kcal）或「玉米杯」（87 kcal）补上。"
            % kcal
        )

    if sodium > 1000:
        top = max(
            (r for r in rows if r.get("sodium")),
            key=lambda r: r["sodium"],
            default=None,
        )
        if top and top["name"] in SWAPS:
            alt, diff = SWAPS[top["name"]]
            advice.append("钠 %dmg（占每日建议 %.0f%%）。把「%s」换成「%s」：%s。" % (sodium, sodium / 20, top["name"], alt, diff))
        else:
            advice.append("钠 %dmg（占每日建议 %.0f%%）。酱料和腌制品是主要来源，可让店员少放酱。" % (sodium, sodium / 20))

    ratio = (fat * 9 / kcal) if kcal else 0
    if ratio > 0.38:
        advice.append("脂肪供能比 %.0f%%，高于 35%% 的推荐线。换一份非油炸主蛋白（板烧/麦香鱼/麦满分）能明显改善。" % (ratio * 100))

    if protein < 18:
        advice.append("蛋白质只有 %.0fg。加「纯牛奶(盒装)」+7g 或「麦乐鸡5块」+12g，饱腹感会好很多。" % protein)

    if roles["veg"] == 0:
        advice.append("整餐没有蔬果项。苹果片（32 kcal）或玉米杯（87 kcal）是最便宜的补丁。")

    if roles["sugary"] and roles["dessert"]:
        advice.append("含糖饮料 + 甜品同时出现，糖负荷翻倍。饮料换无糖可乐可以直接砍掉这份糖。")

    if not advice:
        advice.append("这一餐结构挺稳：能量、蛋白、钠都在合理区间，蔬果也有，放心吃。")
    return advice


def _band_name(band):
    return {
        "breakfast": "早餐",
        "lunch": "午餐",
        "dinner": "晚餐",
        "tea": "下午茶",
        "night": "夜宵",
    }.get(band, "正餐")


def grade_of(score):
    if score >= 88:
        return "S"
    if score >= 78:
        return "A"
    if score >= 68:
        return "B"
    if score >= 55:
        return "C"
    return "D"


GRADE_TITLE = {
    "S": "教科书级",
    "A": "很健康",
    "B": "还过得去",
    "C": "需要收一收",
    "D": "热量炸弹",
}


def analyze_combo(detail, nutri, band="lunch", menu=None):
    """直接分析一个套餐详情（query-meal-detail 的返回）。"""
    items = [{"name": c["name"], "quantity": c["quantity"]} for c in detail["components"]]
    result = analyze(items, nutri, band=band, title=detail.get("name", ""))
    result["components"] = detail["components"]
    return result
