# -*- coding: utf-8 -*-
"""麦险分（McCredit Score）—— 用信贷评分卡的思路给麦当劳账户打分。

为什么这么设计
--------------
真实的消费信贷评分卡（记分卡）回答的是三个问题：
    1. 你**会不会**履约（还款/到店取餐）；
    2. 你的行为**稳不稳**（收入/消费节律）；
    3. 你的**头寸**够不够（资产/额度）。

把它平移到麦当劳上，就是：
    履约稳定度 ← 历史订单完成率
    消费节律   ← 下单时间的规律性与活跃衰减
    预算纪律   ← 客单价的波动与合理带
    优惠利用率 ← 券包厚度与渠道覆盖（薅羊毛能力）
    账户资产   ← 麦享会积分余额与累计
    口味多样性 ← 品类熵 + 历史餐品健康度

输出 300-950 分，区间与芝麻分类似，便于直觉理解；每一分都能追溯到具体数据，
不做黑箱。所有因子与权重见 references/model-spec.md。
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import date, datetime

from . import health as health_mod
from . import mcdata

#: (key, 中文名, 权重)
DIMENSIONS = [
    ("reliability", "履约稳定度", 0.20),
    ("rhythm", "消费节律", 0.15),
    ("budget", "预算纪律", 0.15),
    ("coupon", "优惠利用率", 0.15),
    ("assets", "账户资产", 0.15),
    ("diversity", "口味多样性", 0.20),
]

#: (下限, 等级, 建议单笔额度, 评语)
TIERS = [
    (900, "麦门之神", 500, "收银台见你都要先鞠躬，你说点啥就点啥"),
    (820, "麦门长老", 200, "稳定得像薯条的咸度，闭眼批额度"),
    (720, "巨无霸级", 120, "履约记录漂亮，属于风控最爱的客户"),
    (620, "黄金拱门", 80, "整体健康，偶有波动，正常授信"),
    (500, "拱门学徒", 50, "还在建立消费记录，先小额试水"),
    (0, "麦麦萌新", 30, "数据太少，风控只能给你一个微笑"),
]

CATEGORY_RULES = [
    ("早餐", ("麦满分", "早餐", "豆浆", "粥", "薯饼", "油条", "松饼")),
    ("汉堡", ("堡", "卷", "麦辣鸡腿", "板烧")),
    ("小食", ("薯条", "鸡翅", "麦乐鸡", "鸡排", "鸡球", "鸡块", "玉米", "苹果片")),
    ("甜品", ("新地", "麦旋风", "冰淇淋", "圆筒", "派", "阿芙佳朵", "拉明顿")),
    ("咖啡", ("咖啡", "美式", "拿铁", "奶铁", "卡布奇诺", "浓缩", "黑巧")),
    ("饮品", ("可乐", "雪碧", "红茶", "果汁", "牛奶", "纯悦", "怡泉", "橙")),
]


def category_of(name: str) -> str:
    for cat, keys in CATEGORY_RULES:
        if any(k in name for k in keys):
            return cat
    return "其他"


def _parse_date(text: str):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def _dim(key, name, weight, score, detail, evidence=None):
    return {
        "key": key,
        "name": name,
        "weight": weight,
        "score": round(max(0.0, min(100.0, score)), 1),
        "detail": detail,
        "evidence": evidence or [],
    }


# --------------------------------------------------------------------------- #
# 六个维度
# --------------------------------------------------------------------------- #


def _dim_reliability(orders):
    if not orders:
        return _dim("reliability", "履约稳定度", 0.20, 40, "无历史订单，按冷启动 40 分处理")
    done = sum(1 for o in orders if "完成" in o["status"])
    cancelled = sum(1 for o in orders if "取消" in o["status"] or "失败" in o["status"])
    base = 100.0 * done / len(orders)
    base -= 8.0 * cancelled if cancelled else 0.0
    return _dim(
        "reliability", "履约稳定度", 0.20, base,
        "%d 单中 %d 单完成%s" % (len(orders), done, "，%d 单取消/失败" % cancelled if cancelled else ""),
        ["完成率 %.0f%%" % (100.0 * done / len(orders))],
    )


def _dim_rhythm(orders, today):
    if len(orders) < 2:
        return _dim("rhythm", "消费节律", 0.15, 45, "订单太少，无法判断节奏")
    months = Counter()
    dates = []
    for o in orders:
        d = _parse_date(o["createTime"])
        if d:
            months["%04d-%02d" % (d.year, d.month)] += 1
            dates.append(d)
    if not dates:
        return _dim("rhythm", "消费节律", 0.15, 45, "下单时间无法解析")

    counts = list(months.values())
    regularity = max(0.0, 1 - min(mcdata.cv(counts), 1.5) / 1.5)
    last = max(dates)
    gap = max(0, (today - last).days)
    recency = math.exp(-gap / 120.0)
    span_months = len(months)

    score = 100.0 * (0.55 * regularity + 0.45 * recency)
    return _dim(
        "rhythm", "消费节律", 0.15, score,
        "近 %d 个月 %d 单，月均 %.1f 单；距上次下单 %d 天"
        % (span_months, len(orders), len(orders) / max(1, span_months), gap),
        ["节律稳定度 %.0f%%" % (regularity * 100), "活跃衰减系数 %.2f" % recency],
    )


def _dim_budget(orders):
    amounts = [o["amount"] for o in orders if o["amount"] > 0]
    if not amounts:
        return _dim("budget", "预算纪律", 0.15, 45, "无有效金额数据")
    avg = mcdata.mean(amounts)
    var = mcdata.cv(amounts)
    cv_score = 100.0 * max(0.0, 1 - var / 1.2)
    if 15 <= avg <= 80:
        band_score = 100.0
    elif avg < 15:
        band_score = 55 + avg / 15 * 45
    else:
        band_score = max(40.0, 100 - (avg - 80) * 1.2)
    score = 0.6 * cv_score + 0.4 * band_score
    return _dim(
        "budget", "预算纪律", 0.15, score,
        "客单价均值 ¥%.1f，波动系数 %.2f，最高 ¥%.1f / 最低 ¥%.1f"
        % (avg, var, max(amounts), min(amounts)),
        ["价格一致性 %.0f" % cv_score, "价格合理带 %.0f" % band_score],
    )


def _dim_coupon(coupons, orders):
    n = len(coupons)
    channel = set()
    for c in coupons:
        for t in (c.get("tags") or []):
            if "到店" in t:
                channel.add("到店")
            if "外送" in t:
                channel.add("外送")
    base = min(1.0, n / 3.0) * 60.0
    bonus = 15.0 * len(channel)
    # 券比订单还多，说明囤券未消化，小幅扣分
    if orders and n > len(orders):
        base -= 8.0
    return _dim(
        "coupon", "优惠利用率", 0.15, base + bonus,
        "券包 %d 张，覆盖渠道 %s" % (n, "/".join(sorted(channel)) or "无"),
        ["券包厚度 %.0f" % base, "渠道覆盖 +%.0f" % bonus],
    )


def _dim_assets(account):
    avail = account.get("available", 0.0)
    accum = account.get("accumulated", 0.0)
    a = min(1.0, math.log10(1 + avail) / math.log10(1 + 3000))
    b = min(1.0, math.log10(1 + accum) / math.log10(1 + 8000))
    score = (0.6 * a + 0.4 * b) * 100
    return _dim(
        "assets", "账户资产", 0.15, score,
        "可用积分 %.0f，累计 %.0f，本月将过期 %.0f" % (avail, accum, account.get("expiring", 0)),
        ["可用积分指数 %.0f" % (a * 100), "累计积分指数 %.0f" % (b * 100)],
    )


def _dim_diversity(orders, nutri, band="lunch"):
    if not orders:
        return _dim("diversity", "口味多样性", 0.20, 40, "无历史订单")

    cat_counter = Counter()
    health_scores = []
    for o in orders:
        names = []
        for p in o["items"]:
            if p["components"]:
                names.extend([c["name"] for c in p["components"] for _ in range(c["quantity"])])
            else:
                names.extend([p["name"]] * p["quantity"])
            cat_counter[category_of(p["name"])] += p["quantity"]
        if names and nutri is not None:
            items = [{"name": nm, "quantity": 1} for nm in names]
            h = health_mod.analyze(items, nutri, band=band)
            if h["coverage"] >= 50:
                health_scores.append(h["score"])

    h = mcdata.entropy(cat_counter)
    hmax = math.log(len(CATEGORY_RULES) + 1, 2)
    entropy_score = min(1.0, h / hmax) * 100 if hmax else 0
    health_avg = mcdata.mean(health_scores) if health_scores else 55.0
    score = 0.5 * entropy_score + 0.5 * health_avg
    return _dim(
        "diversity", "口味多样性", 0.20, score,
        "覆盖 %d 个品类（%s）%s"
        % (
            len(cat_counter),
            "、".join("%s×%d" % (k, v) for k, v in cat_counter.most_common(4)),
            "；历史餐品健康均分 %.0f" % health_avg if health_scores else "",
        ),
        ["品类熵 %.2f bit（满分 %.2f）" % (h, hmax), "历史健康均分 %.0f" % health_avg],
    )


# --------------------------------------------------------------------------- #
# 汇总
# --------------------------------------------------------------------------- #


def tier_of(score):
    for floor, name, limit, comment in TIERS:
        if score >= floor:
            return {"name": name, "limit": limit, "comment": comment}
    return {"name": "麦麦萌新", "limit": 30, "comment": ""}


def score_account(orders, account, coupons, nutri=None, today=None, band="lunch"):
    today = today or date.today()
    dims = [
        _dim_reliability(orders),
        _dim_rhythm(orders, today),
        _dim_budget(orders),
        _dim_coupon(coupons, orders),
        _dim_assets(account),
        _dim_diversity(orders, nutri, band),
    ]
    weighted = sum(d["weight"] * d["score"] for d in dims)
    score = int(round(300 + 6.5 * weighted))
    score = max(300, min(950, score))
    tier = tier_of(score)

    risk = round((1 - dims[0]["score"] / 100) * 60 + (1 - dims[1]["score"] / 100) * 40, 1)
    strong = sorted(dims, key=lambda d: d["score"], reverse=True)[:2]
    weak = sorted(dims, key=lambda d: d["score"])[:2]

    return {
        "score": score,
        "tier": tier,
        "weighted": round(weighted, 1),
        "dimensions": dims,
        "risk": min(99.0, risk),
        "strong": [d["name"] for d in strong],
        "weak": [d["name"] for d in weak],
        "tips": _tips(dims),
        "orders_count": len(orders),
    }


def _tips(dims):
    tips = []
    lookup = {d["key"]: d for d in dims}
    if lookup["rhythm"]["score"] < 70:
        tips.append("消费节律偏低：超过一个月没下单会让「活跃衰减」项打折，偶尔来一份甜筒就能续上。")
    if lookup["coupon"]["score"] < 70:
        tips.append("优惠利用率偏低：去「麦麦省」领券再下单，这一项能立刻涨分。")
    if lookup["budget"]["score"] < 70:
        tips.append("预算纪律偏低：客单价波动太大。固定一个 25-40 元的预算带，分数会稳。")
    if lookup["diversity"]["score"] < 70:
        tips.append("口味多样性偏低：长期只点炸鸡套餐。每周换一次非油炸主蛋白，健康维度会明显回升。")
    if lookup["reliability"]["score"] < 80:
        tips.append("履约稳定度偏低：下单后记得取餐，未完成订单会影响这一项。")
    if not tips:
        tips.append("六项全绿，保持这个节奏，麦门之神就在前面。")
    return tips


# --------------------------------------------------------------------------- #
# 订单级麦险分
# --------------------------------------------------------------------------- #


def score_order(price_yuan, health_result, credit):
    """给「这一单」打分：额度占用 × 健康度 × 优惠力度。

    price_yuan: 本单实付金额
    health_result: health.analyze() 的返回
    credit: score_account() 的返回
    """
    limit = credit["tier"]["limit"]
    usage = price_yuan / limit if limit else 1.0
    if usage <= 0.6:
        usage_score, usage_note = 100.0, "额度占用 %.0f%%，很克制" % (usage * 100)
    elif usage <= 1.0:
        usage_score, usage_note = 80.0, "额度占用 %.0f%%，正常" % (usage * 100)
    elif usage <= 1.5:
        usage_score, usage_note = 55.0, "额度占用 %.0f%%，超限" % (usage * 100)
    else:
        usage_score, usage_note = 25.0, "额度占用 %.0f%%，风控警报" % (usage * 100)

    h = health_result["score"]
    if h is None:
        # 营养表没覆盖时不装懂：只按额度占用打分，并把健康维度标为中性
        total = 0.7 * usage_score + 0.3 * 60
        health_note = "营养数据缺失，健康维度按中性 60 分处理"
    else:
        total = 0.45 * usage_score + 0.55 * h
        health_note = "健康分 %d" % h
    grade = "A+" if total >= 90 else "A" if total >= 80 else "B" if total >= 68 else "C" if total >= 55 else "D"

    return {
        "score": int(round(total)),
        "grade": grade,
        "usage": round(usage * 100, 1),
        "usage_note": usage_note,
        "health": h if h is not None else "—",
        "health_note": health_note,
        "limit": limit,
        "note": (
            "在「%s」额度（¥%d）内，这是一笔风控会秒批的订单。"
            % (credit["tier"]["name"], limit)
            if grade in ("A+", "A")
            else "风控建议：降一档套餐或改天再战，这一单性价比不在最佳区间。"
        ),
    }
