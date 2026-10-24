# -*- coding: utf-8 -*-
"""MCP 数据载入层。

设计目标
--------
1. **直接吃原始响应**：MCP 工具返回的 `{"success":true,"data":...}` 可以原样落盘，
   本模块负责解包，评审无需手工整理数据。
2. **同时兼容精简格式**：人工整理的 `{"meals":{...}}` 也能读，方便二次开发。
3. **营养表伪表格解析**：`list-nutrition-foods` 返回的 `data` 是一段用
   逗号分隔的伪表格文本，这里把它解析成结构化数据。
4. **餐品名称归一化 + 模糊匹配**：套餐子项叫「苹果片一袋(60g)」，营养表里叫
   「苹果片」；「新升级板烧鸡腿堡」在营养表里叫「板烧鸡腿堡」。没这层映射，
   健康分析会在最常见的套餐上直接缺口。

所有实现只依赖 Python 标准库，避免参赛评审因装不上依赖而跑不起来。
"""

from __future__ import annotations

import difflib
import json
import math
import re
from collections import Counter

# --------------------------------------------------------------------------- #
# 通用工具
# --------------------------------------------------------------------------- #


def load_json(path: str):
    """读取 UTF-8 JSON 文件。"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_any(path: str):
    """按扩展名决定读法：.txt/.csv/.md 原样读文本，其余按 JSON 解析。

    MCP 有两个工具返回的不是 JSON（营养表是伪表格文本，券包是 markdown），
    所以落盘时会存成 .txt/.md，这里统一处理。
    """
    if path.lower().endswith((".txt", ".csv", ".md")):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return load_json(path)


def unwrap(payload):
    """剥掉 MCP 的统一响应外壳，取出 data。"""
    if isinstance(payload, dict) and "data" in payload and "success" in payload:
        return payload["data"]
    if isinstance(payload, dict) and "data" in payload and "code" in payload:
        return payload["data"]
    return payload


def parse_qty_items(text: str):
    """解析 `--items "巨无霸*1,中薯条*1"` 这类命令行参数。"""
    items = []
    for chunk in re.split(r"[,，;；\n]", text or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.match(r"^(.*?)\s*(?:[*xX×]\s*(\d+))?$", chunk)
        if not m:
            continue
        name = m.group(1).strip()
        qty = int(m.group(2) or 1)
        if name:
            items.append({"name": name, "quantity": max(1, qty)})
    return items


# --------------------------------------------------------------------------- #
# 名称归一化与营养库
# --------------------------------------------------------------------------- #

#: 名称中需要被抹平的噪声
_NOISE = ("™", "®", "·", "•", "—", "－", "＋")

#: 已知的跨命名差异（键值均为归一化后的形式）
ALIASES = {
    "苹果片一袋": "苹果片",
    "苹果片1袋": "苹果片",
    "苹果片一袋60g": "苹果片",
    "新升级板烧鸡腿堡": "板烧鸡腿堡",
    "可口可乐中杯": "可乐中杯",
    "可口可乐大杯": "可乐大杯",
    "可口可乐小杯": "可乐小杯",
    "那么大鸡排椒盐风味": "那么大鸡排",
    "麦辣鸡翅2块": "麦辣鸡翅-2块",
    "麦乐鸡4块": "麦乐鸡4块",
    "脆薯饼": "脆薯饼",
    "原味板烧鸡腿麦满分": "原味板烧鸡腿麦满分",
    "经典麦旋风": "奥利奥麦旋风",
    "新地": "朱古力新地",
    "派": "香芋派",
}


def normalize(name: str) -> str:
    """把餐品名压成可比较的键：去括号、去空格连字符、统一大小写。"""
    s = str(name or "").strip().lower()
    s = s.replace("（", "(").replace("）", ")").replace("【", "").replace("】", "")
    s = re.sub(r"\([^)]*\)", "", s)          # 去掉括号注释，如「(60g)」
    s = re.sub(r"\[[^\]]*\]", "", s)
    for ch in _NOISE:
        s = s.replace(ch, "")
    s = re.sub(r"[\s\-_/、]+", "", s)
    return s


class NutriDB:
    """营养库。行字段：name / kj / kcal / protein / fat / carb / sodium / calcium。"""

    FIELDS = ("kcal", "protein", "fat", "carb", "sodium", "calcium", "kj")

    def __init__(self, rows):
        self.rows = list(rows)
        self.index = {}
        for row in self.rows:
            self.index.setdefault(normalize(row["name"]), row)
        self.keys = sorted(self.index)

    def __len__(self):
        return len(self.rows)

    def lookup(self, name: str):
        """返回 (营养行, 置信度)。置信度 1.0 精确命中，0 表示无数据。"""
        key = normalize(name)
        if not key:
            return None, 0.0
        if key in self.index:
            return self.index[key], 1.0

        alias = ALIASES.get(key)
        if alias:
            akey = normalize(alias)
            if akey in self.index:
                return self.index[akey], 0.95

        hit = difflib.get_close_matches(key, self.keys, n=1, cutoff=0.88)
        if hit:
            return self.index[hit[0]], 0.75

        # 「大杯/中杯/小杯」缺失时退一级
        for suffix in ("大杯", "中杯", "小杯", "盒装", "1块", "一块", "一份"):
            if key.endswith(suffix) and key[: -len(suffix)] in self.index:
                return self.index[key[: -len(suffix)]], 0.7
        return None, 0.0

    def zero(self):
        return {f: 0.0 for f in self.FIELDS}


def _parse_nutrition_text(text: str):
    """解析 `list-nutrition-foods` 的伪表格文本。

    形如：
        [160]{productName,nutritionDescription,energyKj,energyKcal,...}:
          猪柳麦满分,null,1288,308,16,16,24,781,213
    """
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("[") or line.startswith("{"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 9:
            continue
        try:
            nums = [float(parts[i]) for i in range(2, 9)]
        except ValueError:
            continue
        rows.append(
            {
                "name": parts[0],
                "kj": nums[0],
                "kcal": nums[1],
                "protein": nums[2],
                "fat": nums[3],
                "carb": nums[4],
                "sodium": nums[5],
                "calcium": nums[6],
            }
        )
    return rows


def parse_nutrition(payload) -> NutriDB:
    """兼容三种输入：MCP 原始响应 / 文本 / 已结构化列表。"""
    data = unwrap(payload)
    if isinstance(data, str):
        return NutriDB(_parse_nutrition_text(data))
    if isinstance(data, dict) and "items" in data:
        return NutriDB(data["items"])
    if isinstance(data, list):
        if data and isinstance(data[0], str):
            return NutriDB(_parse_nutrition_text("\n".join(data)))
        return NutriDB(data)
    raise ValueError("无法识别的营养数据格式")


# --------------------------------------------------------------------------- #
# 菜单
# --------------------------------------------------------------------------- #

COMBO_HINTS = (
    "套餐", "件套", "双人", "多人", "分享", "随心选", "四宫格",
    "随心配", "随心拼", "小食盘", "口福", "任选", "组合",
)
#: 这些词说明是「搭配型商品」而不是单个 SKU
COMBO_HINTS_STRICT = ("套餐", "件套", "双人", "多人", "分享", "随心选", "四宫格", "随心配", "随心拼")


class Menu:
    """门店菜单。统一 currentPrice/originalPrice 两种字段名。"""

    def __init__(self, payload, store_code="", store_name=""):
        data = unwrap(payload) or {}
        self.store_code = str(data.get("storeCode") or store_code or "")
        self.store_name = data.get("storeName") or store_name or ""
        self.captured_at = data.get("capturedAt") or ""
        self.meals = {}
        self.frequent_code = ""

        for code, raw in (data.get("meals") or {}).items():
            code = str(code)
            price = raw.get("price", raw.get("currentPrice", 0))
            self.meals[code] = {
                "code": code,
                "name": raw.get("name", ""),
                "price": _to_float(price),
                "originalPrice": _to_float(raw.get("originalPrice", 0)) or _to_float(price),
                "tags": list(raw.get("tags") or []),
                "category": raw.get("category", ""),
                "discountType": raw.get("discountType") or "",
            }

        for cat in data.get("categories") or []:
            if isinstance(cat, (list, tuple)) and len(cat) >= 2:
                cat_name, codes = cat[0], cat[1]
            elif isinstance(cat, dict):
                cat_name = cat.get("name", "")
                codes = [
                    (m.get("code") if isinstance(m, dict) else m)
                    for m in (cat.get("meals") or [])
                ]
            else:
                continue
            for code in codes:
                code = str(code)
                meal = self.meals.get(code)
                if meal and not meal["category"]:
                    meal["category"] = cat_name

        frequent = data.get("frequent")
        if isinstance(frequent, dict):
            self.frequent_code = str(frequent.get("code") or "")

    def __len__(self):
        return len(self.meals)

    def get(self, code):
        return self.meals.get(str(code))

    def all(self):
        return list(self.meals.values())

    def find(self, keyword):
        key = normalize(keyword)
        best, best_len = None, 10**9
        for meal in self.meals.values():
            nk = normalize(meal["name"])
            if key and key in nk and len(nk) < best_len:
                best, best_len = meal, len(nk)
        return best

    def combos(self):
        """只返回「可当一餐吃」的搭配型商品。"""
        out = []
        for meal in self.meals.values():
            name = meal["name"]
            if any(h in name for h in COMBO_HINTS_STRICT):
                out.append(meal)
        return out

    def is_combo(self, meal) -> bool:
        return any(h in meal["name"] for h in COMBO_HINTS)

    def value_ratio(self, meal) -> float:
        """省钱比：相对划线价的折扣力度，0~1。"""
        if meal["originalPrice"] <= 0 or meal["price"] <= 0:
            return 0.0
        return max(0.0, 1 - meal["price"] / meal["originalPrice"])


def _to_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------- #
# 套餐详情 / 订单 / 账户 / 券
# --------------------------------------------------------------------------- #


def parse_detail(payload):
    """把 `query-meal-detail` 的 rounds 摊平成「默认组合」子项列表。

    营销套餐的价格只能靠 `query-meal-detail` 拆开，拆开后才可能映射到营养表，
    这是「套餐健康度」能算出来的前提。
    """
    data = unwrap(payload) or {}
    comps = []
    for rnd in data.get("rounds") or []:
        for choice in rnd.get("choices") or []:
            if int(choice.get("isDefault") or 0) == 1 and int(choice.get("quantity") or 0) >= 1:
                comps.append(
                    {
                        "name": choice.get("name", ""),
                        "code": str(choice.get("code", "")),
                        "quantity": int(choice.get("quantity") or 1),
                    }
                )
    if not comps and data.get("components"):
        comps = [
            {"name": c.get("name", ""), "code": str(c.get("code", "")), "quantity": int(c.get("quantity") or 1)}
            for c in data["components"]
        ]
    return {
        "code": str(data.get("code", "")),
        "name": data.get("name", ""),
        "components": comps,
    }


def parse_orders(payload):
    """历史订单归一化，按时间倒序。"""
    data = unwrap(payload) or {}
    raw_list = data.get("list") if isinstance(data, dict) else data
    orders = []
    for o in raw_list or []:
        products = []
        for p in o.get("orderProductList") or []:
            products.append(
                {
                    "code": str(p.get("productCode", "")),
                    "name": p.get("productName", ""),
                    "quantity": int(p.get("quantity") or 1),
                    "components": [
                        {
                            "name": c.get("name") or c.get("productName", ""),
                            "quantity": int(c.get("quantity") or 1),
                        }
                        for c in (p.get("comboItemList") or [])
                    ],
                }
            )
        orders.append(
            {
                "orderId": o.get("orderId", ""),
                "createTime": o.get("createTime", ""),
                "orderType": str(o.get("orderType", "1")),
                "storeName": o.get("storeName", ""),
                "status": o.get("orderStatus", ""),
                "amount": _to_float(o.get("realTotalAmount")),
                "items": products,
            }
        )
    orders.sort(key=lambda x: x["createTime"], reverse=True)
    return orders


def parse_coupons(payload):
    """券包归一化。原始是 markdown 列表，这里按 `## 标题` 切块提取。"""
    data = unwrap(payload)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "list" in data:
        return data["list"]
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    coupons, current = [], None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("## "):
            if current:
                coupons.append(current)
            current = {"name": line[3:].strip(), "raw": ""}
        elif line.startswith("#"):
            continue
        elif current is not None and line:
            current["raw"] += line + "\n"
            m = re.search(r"¥\s*([\d.]+)", line)
            if m and "price" not in current:
                current["price"] = float(m.group(1))
            m = re.search(r"有效期\**[:：]\s*\**(.+)", line)
            if m and "validity" not in current:
                current["validity"] = m.group(1).strip()
            if "标签" in line:
                current["tags"] = [t.strip() for t in re.split(r"[、,，]", line.split("：")[-1]) if t.strip()]
    if current:
        coupons.append(current)
    return coupons


def parse_account(payload):
    data = unwrap(payload) or {}
    return {
        "available": _to_float(data.get("availablePoint")),
        "accumulated": _to_float(data.get("accumulativePoint")),
        "used": _to_float(data.get("usedPoint")),
        "expiring": _to_float(data.get("currentMouthExpirePoint")),
        "expired": _to_float(data.get("expiredPoint")),
        "accountId": data.get("accountId", ""),
    }


def parse_stores(payload):
    """门店列表归一化。distance 单位米。"""
    data = unwrap(payload) or []
    if isinstance(data, dict):
        data = data.get("list") or data.get("data") or []
    stores = []
    for s in data:
        stores.append(
            {
                "storeCode": str(s.get("storeCode", "")),
                "storeName": s.get("storeName", ""),
                "address": s.get("address", ""),
                "distance": _to_float(s.get("distance")),
                "beCode": s.get("beCode", ""),
                "open": s.get("businessStatus", True),
                "openTime": "%s-%s" % (s.get("businessStartTime", ""), s.get("businessEndTime", "")),
            }
        )
    stores.sort(key=lambda x: x["distance"])
    return stores


# --------------------------------------------------------------------------- #
# 统计小工具（供 credit / bulk 复用）
# --------------------------------------------------------------------------- #


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else 0.0


def stdev(values):
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return 0.0
    mu = mean(values)
    return math.sqrt(sum((v - mu) ** 2 for v in values) / (len(values) - 1))


def cv(values):
    """变异系数：衡量波动。均值 0 时返回 0。"""
    mu = mean(values)
    if mu <= 0:
        return 0.0
    return stdev(values) / mu


def entropy(counter: Counter) -> float:
    total = sum(counter.values())
    if total <= 0:
        return 0.0
    h = 0.0
    for v in counter.values():
        if v > 0:
            p = v / total
            h -= p * math.log(p, 2)
    return h
