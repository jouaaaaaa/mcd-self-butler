# 麦当劳 MCP 工具速查

只记录**本项目实际调用过**的工具，以及与代码强相关的字段和参数坑。
端点：`https://mcp.mcd.cn`（Streamable HTTP，`Authorization: Bearer <TOKEN>`）。

## 场景 → `orderType` / `beCode` 对照表

| 场景 | `beType` | `orderType` | `beCode` | 取店接口 |
|---|---|---|---|---|
| 到店自取 | 1 | 1 | **不传**（传了报错） | `query-nearby-stores` |
| 得来速 DT | 5 | 1 | **必传** | `query-nearby-stores` |
| 麦乐送 | 2 | 2 | **必传** | `delivery-query-stores` |
| 企业团餐 | 6 | 2 | **必传** | `delivery-query-stores` |

---

## 门店

### `query-nearby-stores`
- `searchType=1` 收藏门店（无参）
- `searchType=2` 按位置搜，**必须** `city` + `keyword`
- 返回 `storeCode`、`distance`（米）、`reservation`、`reservationTimeOptions`
- `reservationTimeOptions` 里 `today=true` 的日期要标 *，一条都不能省，否则用户没法预约

### `delivery-query-addresses` → `delivery-query-stores`
外送/团餐两步走：先拿 `addressId`，再用 `addressId + beType` 换门店与 `beCode`。

---

## 菜单与算价

### `query-meals`
- 入参：`storeCode`、`orderType`、`beType`（+ 外送/团餐的 `beCode`；预约加 `reservationDate`）
- 返回两层：`categories[].meals[]`（分类视图）与 `meals{code: {...}}`（详情映射）
- 关键字段：`name` / `currentPrice` / `originalPrice` / `discountType` / `canWithOrder` / `withOrder`
- `frequent` 是「我的常点」，只有一条
- 团餐场景的搭配建议（工具自带）：20 元以下给小食，30-40 给「堡+小食+饮料」，50 以上不重复小食

### `query-meal-detail`
- 入参：`storeCode`、`orderType`、`beType`、`code`（+ 需要的 `beCode`）
- **这就是「套餐拆解」的关键**：`rounds[].choices[]` 里 `isDefault=1 && quantity>=1` 的就是默认组合
- `supportModify=true` 时输出要给商品名加【可特调】，但**不要主动展开**特调选项
- 特调传参：选中项用 `selectedKey`，未选中但有 `unselectedKey` 的**也必须传**

### `calculate-price`
- 金额单位是**分**，展示除以 100
- 返回 `takeWayList[].code`（到店下单必传 `takeWayCode`，如 `eat-in` / `take-in-store`）
- `enjoyed` / `enjoyable` 告诉你已享受/可享受的优惠，`balance` 是差额门槛

---

## 营养

### `list-nutrition-foods`
- **返回的不是 JSON**，是 `data` 字段里的一段伪表格文本：

```
[160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
  猪柳麦满分,null,1288,308,16,16,24,781,213
```

- 字段位次：`name, desc, kJ, kcal, protein, fat, carb, sodium(mg), calcium(mg)`
- 只有单品，没有套餐 → 套餐必须 `query-meal-detail` 拆解后求和
- 有重复条目（小杯玉米杯、纯牛奶），解析时保留首条

---

## 券与账户

| 工具 | 用在哪 | 坑 |
|---|---|---|
| `query-my-coupons` | 券包全集 | 返回 markdown 列表，不校验门店/渠道可用性 |
| `query-store-coupons` | 已定门店的真可用券 | 需要 `storeCode`（纯数字）+ `orderType` + `beType` |
| `available-coupons` | 麦麦省可领券 | 只返回可领取列表，不代表已拥有 |
| `query-my-account` | 积分 | `availablePoint` 可用 / `currentMouthExpirePoint` 本月过期 |

---

## 订单

### `order-list`
- 返回 `orderProductList[]`，其中 `comboItemList[]` 会展开套餐子项 —— **这是麦险分能算健康度的前提**
- `realTotalAmount` 是实付，字符串型
- 只有历史订单，没有分页参数

### `query-order`
单个订单状态/配送进度，需要 `orderId`。用户说「我付完了」时用它确认。

---

## 活动与团餐

### `campaign-calendar`
- 不传参 → 当月全部活动
- 传 `specifiedDate` → 只看当天及其前后最近的两个有活动的日子（共三天），适合「今天有什么」

### `query-promotions`（仅团餐）
- `beType` 固定 6、`orderType` 固定 2
- 返回 `promotionType` 31 满额减 / 33 订单折扣，`ruleCategory` 40 满减 / 30 满折
- 工具**不做最优推荐**，规则要自己算

### `query-meal-assistance`（团餐助餐服务）
拿 `gmServiceCode`，下单时必传。

---

## 下单

### `create-order`
- 前置：`calculate-price` 拿 `takeWayCode`、`query-meal-assistance` 拿 `gmServiceCode`
- 必须用户明确确认后才能调
- 外送场景可加 `remark`（≤50 字），如「无接触配送」
- 返回支付链接，用户扫码后回「支付完成」，再用 `query-order` 查状态

---

## 本项目实际用到的工具清单

| 工具 | 用在 | 是否必须 |
|---|---|---|
| `query-nearby-stores` | 探店 / 比价 | ✅ |
| `delivery-query-stores` | 外送 / 团餐选址 | ○ |
| `query-meals` | 菜单与标价 | ✅ |
| `query-meal-detail` | 套餐拆解（健康分析的前提） | ✅ |
| `list-nutrition-foods` | 营养库 | ✅ |
| `calculate-price` | 到手价、订单级麦险分 | ✅ |
| `query-my-coupons` | 券包、优惠利用率 | ✅ |
| `query-store-coupons` | 门店可用券 | ○ |
| `available-coupons` | 可领券推荐 | ○ |
| `query-my-account` | 积分维度 | ✅ |
| `order-list` | 履约/节律/预算/多样性 | ✅ |
| `query-order` | 下单后状态 | ○ |
| `campaign-calendar` | 活动叠加建议 | ○ |
| `query-promotions` | 团餐满减 | ○ |
| `create-order` | 用户确认后下单 | ○ |

✅ 核心链路　○ 按场景启用
