# -*- coding: utf-8 -*-
"""麦门小剧场：让五个小人为了「这一单怎么点最划算」当场吵起来。

为什么要有这个东西
------------------
点完单到取餐之间有 3-10 分钟空窗期，这段时间用户什么都做不了。
本模块把这个空窗期变成内容：五个角色各自代表一种真实存在的决策立场
（省钱 / 健康 / 吃饱 / 薅券 / 风控），用用户自己的券、麦险分、热量数据
吵出一份结论 —— 结论就是推荐结果，过程本身就是解释。

产物是一个单文件 HTML（零依赖、可直接双击打开），也同时输出纯文本剧本，
方便在聊天窗口里直接读。
"""

from __future__ import annotations

import html
import json

CAST = [
    {"id": "uncle", "name": "麦叔叔", "role": "店长·仲裁", "color": "#DA291C", "accent": "#FFC72C", "ink": "#B4170F"},
    {"id": "thief", "name": "汉堡神偷", "role": "券猎人", "color": "#7B5BFF", "accent": "#D9CCFF", "ink": "#5B3FD6"},
    {"id": "shake", "name": "奶昔大哥", "role": "饱腹派", "color": "#FF7BAC", "accent": "#FFD9E7", "ink": "#C2417A"},
    {"id": "fly", "name": "小飞飞", "role": "健康监督", "color": "#2FB673", "accent": "#C9F2DD", "ink": "#1E7A4C"},
    {"id": "score", "name": "麦险分先生", "role": "风控裁判", "color": "#2B2B33", "accent": "#FFC72C", "ink": "#1F1F26"},
]

MOOD_OF_BEAT = {
    "开场": "happy",
    "报价": "angry",
    "健康": "angry",
    "券": "happy",
    "裁决": "idle",
    "通过": "happy",
}


def _fmt_money(v):
    try:
        return "%.1f" % float(v)
    except (TypeError, ValueError):
        return "0.0"


def build_script(ctx):
    """根据上下文生成台词。ctx 缺字段时用合理默认值兜底，不抛异常。"""
    user = ctx.get("user") or "这位麦友"
    meal = ctx.get("meal") or "午餐"
    budget = ctx.get("budget") or 40
    credit = ctx.get("credit") or {}
    score = credit.get("score") or 700
    tier = credit.get("tier") or "黄金拱门"
    limit = credit.get("limit") or 80
    coupons = ctx.get("coupons") or []
    candidates = [c for c in (ctx.get("candidates") or []) if c.get("name")]
    winner = ctx.get("winner") or (candidates[0] if candidates else {})

    def pick(key, reverse=True, default=None):
        pool = [c for c in candidates if c.get(key) is not None]
        if not pool:
            return default or (candidates[0] if candidates else {})
        return sorted(pool, key=lambda c: c[key], reverse=reverse)[0]

    cheapest = pick("price", reverse=False)
    healthiest = pick("health", reverse=True)
    hottest_kcal = pick("kcal", reverse=True)

    w_name = winner.get("name", "套餐")
    w_price = _fmt_money(winner.get("price"))
    w_kcal = winner.get("kcal")
    w_health = winner.get("health")
    coupon_name = coupons[0] if coupons else ""
    coupon_line = (
        "我这儿还压着一张「%s」，不用过期作废！" % coupon_name if coupon_name
        else "这周没有可用的券，纯靠套餐本身的折扣。"
    )

    unit = winner.get("price", 0) / max(1, (w_kcal or 500)) * 100
    lines = [
        ("shake", "开场", "我饿了。真的饿了。%s就得吃点扛饿的，别跟我讲什么热量。" % meal),
        ("fly", "健康", "等一下。%s是一天里最容易吃超的一餐，先看数字再下单。" % meal),
        ("thief", "券", coupon_line),
        ("uncle", "报价", "都别急。%s，预算 ¥%s，麦险分 %d 分（%s），单笔额度 ¥%d，我按这个来裁。"
         % (user, _fmt_money(budget), score, tier, limit)),

        ("fly", "健康", "「%s」%s kcal。一顿吃掉一天四分之一的能量，午饭后面还有下午，你确定？"
         % (hottest_kcal.get("name", "这个"), hottest_kcal.get("kcal", "—"))),
        ("shake", "开场", "但它扛饿啊！两小时后又饿的人不是你。"),
        ("thief", "券", "「%s」¥%s，是这批里最便宜的，配上券还能再砍一刀。"
         % (cheapest.get("name", "这个"), _fmt_money(cheapest.get("price")))),
        ("fly", "健康", "便宜不等于合适。「%s」健康分 %s，是这一桌里最高的，蛋白结构也最稳。"
         % (healthiest.get("name", "那个"), healthiest.get("health", "—"))),

        ("thief", "报价", "等等。按每 100 kcal 算，「%s」要 ¥%.1f，是这桌里最贵的一个。"
         % (w_name, unit)),
        ("uncle", "报价", "因为你买的是营养密度，不是热量密度。把肚子填满很便宜，把身体喂好从来不便宜。"),
        ("thief", "券", "……这句我记到账本上了。"),

        ("score", "裁决", "风控意见：麦险分 %d，%s。这一单 ¥%s 占额度 %s%%，属于%s。"
         % (
            score, tier, w_price,
            _fmt_money(winner.get("price") / max(1, limit) * 100),
            "秒批" if winner.get("price", 0) <= limit * 0.6 else "可批" if winner.get("price", 0) <= limit else "需要复核",
         )),
        ("fly", "通过", "健康分 %s，%s。行吧，我勉强同意，但下次把饮料锁定成无糖，我就彻底没意见。"
         % (w_health if w_health is not None else "—", "在这一档里算干净的" if (w_health or 0) >= 70 else "属于妥协方案")),
        ("shake", "通过", "成交！我就知道饿着肚子的人最有说服力。"),
        ("uncle", "通过", "那就这么定：%s，¥%s。%s，这单我批了，去取餐吧。"
         % (w_name, w_price, user)),
    ]

    script = [
        {"who": who, "beat": beat, "text": text, "mood": MOOD_OF_BEAT.get(beat, "idle")}
        for who, beat, text in lines
    ]
    return script


def to_text(script, ctx=None):
    """纯文本剧本，方便在聊天里直接读。"""
    names = {c["id"]: c["name"] for c in CAST}
    out = ["🎬 麦门小剧场 · 就为这一单吵一架", ""]
    for line in script:
        out.append("%s：%s" % (names.get(line["who"], line["who"]), line["text"]))
        out.append("")
    winner = (ctx or {}).get("winner") or {}
    if winner.get("name"):
        out.append("—— 全场通过：%s  ¥%s" % (winner["name"], _fmt_money(winner.get("price"))))
    return "\n".join(out).strip()


# --------------------------------------------------------------------------- #
# HTML 渲染
# --------------------------------------------------------------------------- #

_ACTOR_SVG = """
<svg class="avatar" viewBox="0 0 120 148" aria-hidden="true">
  <defs>
    <linearGradient id="g-{id}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{accent}" stop-opacity="0.98"/>
      <stop offset="100%" stop-color="{color}" stop-opacity="0.95"/>
    </linearGradient>
  </defs>
  <ellipse cx="60" cy="138" rx="30" ry="5.5" fill="#000" opacity="0.22"/>
  <rect x="31" y="70" width="58" height="62" rx="24" fill="url(#g-{id})"/>
  {extra}
  <circle cx="60" cy="48" r="29" fill="{skin}"/>
  <circle cx="49" cy="47" r="4" fill="#20140f"/>
  <circle cx="71" cy="47" r="4" fill="#20140f"/>
  <path class="mouth m-idle" d="M53 60 q7 5 14 0" stroke="#20140f" stroke-width="3" fill="none" stroke-linecap="round"/>
  <path class="mouth m-happy" d="M51 57 q9 11 18 0" stroke="#20140f" stroke-width="3" fill="none" stroke-linecap="round"/>
  <path class="mouth m-angry" d="M51 63 q9 -7 18 0" stroke="#20140f" stroke-width="3" fill="none" stroke-linecap="round"/>
  <path d="M32 32 q28 -22 56 0 z" fill="{color}"/>
  <rect x="24" y="30" width="72" height="7" rx="3.5" fill="{color}"/>
  <text x="60" y="27" text-anchor="middle" font-size="14" font-weight="800" fill="{accent}">{badge}</text>
</svg>
"""

_SKIN = {
    "uncle": "#FFD9A0",
    "thief": "#FFE3C2",
    "shake": "#FFE3C2",
    "fly": "#FFE3C2",
    "score": "#E8E8EE",
}
_BADGE = {"uncle": "M", "thief": "¥", "shake": "杯", "fly": "★", "score": "分"}

#: 每个角色的辨识度配件（画在身体之上、头部之下）
_EXTRA = {
    "uncle": '<path d="M46 104 q14 12 28 0" stroke="#7a1109" stroke-width="4" fill="none" stroke-linecap="round" opacity=".5"/>',
    "thief": '<rect x="36" y="41" width="48" height="9" rx="4.5" fill="#2B2B33" opacity=".88"/>'
             '<circle cx="49" cy="45.5" r="3.4" fill="#D9CCFF"/><circle cx="71" cy="45.5" r="3.4" fill="#D9CCFF"/>',
    "shake": '<line x1="72" y1="14" x2="82" y2="40" stroke="#FF7BAC" stroke-width="4" stroke-linecap="round"/>'
             '<rect x="46" y="76" width="28" height="9" rx="4" fill="#fff" opacity=".38"/>',
    "fly": '<path d="M36 72 q24 11 48 0 l0 9 q-24 10 -48 0 z" fill="#2FB673" opacity=".95"/>'
           '<path d="M84 78 l10 14 -12 -4 z" fill="#2FB673" opacity=".95"/>',
    "score": '<rect x="43" y="80" width="34" height="21" rx="4" fill="#12121a" stroke="#FFC72C" stroke-width="2"/>'
             '<text x="60" y="95" text-anchor="middle" font-size="12" fill="#FFC72C" font-weight="800">AAA</text>',
}


def _cast_html():
    parts = []
    for c in CAST:
        svg = _ACTOR_SVG.format(
            id=c["id"],
            color=c["color"],
            accent=c["accent"],
            skin=_SKIN.get(c["id"], "#FFE3C2"),
            badge=_BADGE.get(c["id"], "M"),
            extra=_EXTRA.get(c["id"], ""),
        )
        parts.append(
            '<div class="actor" data-id="%s" data-mood="idle">%s'
            '<div class="name">%s</div><div class="role">%s</div></div>'
            % (c["id"], svg, c["name"], c["role"])
        )
    return "\n".join(parts)


_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>麦门小剧场 · __TITLE__</title>
<style>
  :root{
    --red:#DA291C; --yellow:#FFC72C; --ink:#12121a; --paper:#fffdf7;
    --dim:#a9a4b8;
  }
  *{box-sizing:border-box}
  body{
    margin:0; min-height:100vh; color:#fff;
    font-family:"PingFang SC","Microsoft YaHei","Hiragino Sans GB",system-ui,sans-serif;
    background:
      radial-gradient(900px 520px at 18% -8%, rgba(218,41,28,.55), transparent 62%),
      radial-gradient(760px 460px at 88% 4%, rgba(255,199,44,.28), transparent 60%),
      linear-gradient(170deg,#141019 0%,#1d1420 46%,#120e16 100%);
  }
  .wrap{max-width:1000px;margin:0 auto;padding:26px 20px 54px}
  .top{display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap;margin-bottom:16px}
  .title{font-size:15px;letter-spacing:.24em;color:var(--yellow);text-transform:uppercase}
  .title b{display:block;font-size:26px;letter-spacing:.06em;color:#fff;margin-top:6px}
  .scorecard{
    background:linear-gradient(135deg,rgba(255,199,44,.16),rgba(218,41,28,.18));
    border:1px solid rgba(255,199,44,.34); border-radius:16px; padding:10px 16px;
    display:flex;align-items:center;gap:14px
  }
  .scorecard .num{font-size:26px;font-weight:800;color:var(--yellow);line-height:1}
  .scorecard .cap{font-size:11px;color:var(--dim);letter-spacing:.12em}
  .stage{
    position:relative;border-radius:24px;padding:22px 22px 18px;
    background:linear-gradient(180deg,rgba(255,255,255,.075),rgba(255,255,255,.025));
    border:1px solid rgba(255,255,255,.12);
    box-shadow:0 30px 70px -40px rgba(0,0,0,.9), inset 0 1px 0 rgba(255,255,255,.12);
    overflow:hidden;
  }
  .lights{position:absolute;inset:0 0 auto 0;height:4px;
    background:repeating-linear-gradient(90deg,var(--yellow) 0 14px,transparent 14px 28px);opacity:.5}
  .cast{display:flex;justify-content:space-between;gap:6px;margin:10px 0 6px}
  .actor{width:19%;text-align:center;opacity:.42;transform:translateY(0);transition:opacity .3s,transform .3s;filter:saturate(.5)}
  .actor.on{opacity:1;transform:translateY(-6px);filter:none}
  .avatar{width:100%;max-width:108px;height:auto;display:block;margin:0 auto}
  .actor.on .avatar{animation:bob .62s ease-in-out infinite alternate}
  @keyframes bob{from{transform:translateY(0)}to{transform:translateY(-5px)}}
  .actor .name{font-size:13px;margin-top:6px;font-weight:700;color:#fff}
  .actor .role{font-size:10px;color:var(--dim);letter-spacing:.08em}
  .mouth{display:none}
  .actor[data-mood="idle"] .m-idle,
  .actor[data-mood="happy"] .m-happy,
  .actor[data-mood="angry"] .m-angry{display:block}

  .bubble{
    margin-top:14px;min-height:118px;border-radius:18px;padding:18px 20px;
    background:var(--paper);color:#1a1418;position:relative;font-size:17px;line-height:1.7;
    box-shadow:0 18px 34px -24px rgba(0,0,0,.9);
  }
  .bubble .who{font-size:12px;font-weight:800;letter-spacing:.14em;margin-bottom:7px;display:block}
  .bubble:after{
    content:"";position:absolute;top:-10px;left:var(--tail,40px);transform:translateX(-50%);opacity:0;
    border-left:11px solid transparent;border-right:11px solid transparent;border-bottom:11px solid var(--paper);
    transition:left .32s cubic-bezier(.2,.9,.3,1),opacity .18s
  }
  .bubble.talking:after{opacity:1}
  .cursor{display:inline-block;width:8px;height:17px;background:var(--red);vertical-align:-3px;animation:blink .8s steps(1) infinite}
  @keyframes blink{50%{opacity:0}}

  .log{margin-top:14px;max-height:190px;overflow:auto;display:flex;flex-direction:column;gap:7px;padding-right:4px}
  .log::-webkit-scrollbar{width:6px}
  .log::-webkit-scrollbar-thumb{background:rgba(255,255,255,.18);border-radius:99px}
  .log .row{font-size:13px;line-height:1.6;color:#cfc9dc}
  .log .row b{margin-right:6px}

  .verdict{
    margin-top:16px;border-radius:20px;padding:18px 20px;display:none;
    background:linear-gradient(135deg,rgba(255,199,44,.95),rgba(255,164,44,.92));
    color:#231a06;box-shadow:0 24px 44px -28px rgba(255,199,44,.8)
  }
  .verdict.show{display:block;animation:pop .5s cubic-bezier(.2,1.4,.4,1)}
  @keyframes pop{from{transform:scale(.94);opacity:0}to{transform:scale(1);opacity:1}}
  .verdict h3{margin:0 0 6px;font-size:13px;letter-spacing:.2em;text-transform:uppercase;opacity:.7}
  .verdict .pick{font-size:22px;font-weight:800;margin-bottom:10px}
  .verdict .grid{display:flex;gap:22px;flex-wrap:wrap;font-size:13px}
  .verdict .grid div span{display:block;font-size:11px;opacity:.62;letter-spacing:.1em}

  .controls{display:flex;gap:10px;align-items:center;margin-top:16px;flex-wrap:wrap}
  button{
    appearance:none;border:1px solid rgba(255,255,255,.2);background:rgba(255,255,255,.08);
    color:#fff;padding:9px 16px;border-radius:999px;font-size:13px;cursor:pointer;
    transition:background .2s,transform .1s;font-family:inherit
  }
  button:hover{background:rgba(255,255,255,.16)}
  button:active{transform:scale(.97)}
  button.primary{background:var(--red);border-color:transparent;font-weight:700}
  .hint{font-size:12px;color:var(--dim);margin-left:auto}
  .confetti{position:absolute;width:8px;height:8px;border-radius:2px;pointer-events:none;animation:fall 1.6s linear forwards}
  @keyframes fall{to{transform:translateY(320px) rotate(320deg);opacity:0}}
  @media (max-width:640px){
    .bubble{font-size:15px;min-height:104px}
    .actor .role{display:none}
    .title b{font-size:21px}
  }
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div class="title">McButler Theater<b>就为这一单，吵一架</b></div>
    <div class="scorecard">
      <div><div class="cap">麦险分</div><div class="num" id="mc-score">__SCORE__</div></div>
      <div><div class="cap">等级</div><div style="font-weight:700" id="mc-tier">__TIER__</div></div>
      <div><div class="cap">额度</div><div style="font-weight:700">¥__LIMIT__</div></div>
    </div>
  </div>

  <div class="stage" id="stage">
    <div class="lights"></div>
    <div class="cast" id="cast">
__CAST__
    </div>
    <div class="bubble" id="bubble">
      <span class="who" id="who">准备开场</span>
      <span id="text">点「开吵」，五个人会为了你这一单当场吵起来。<span class="cursor"></span></span>
    </div>
    <div class="log" id="log"></div>
    <div class="verdict" id="verdict">
      <h3>Final Call · 全场通过</h3>
      <div class="pick" id="v-pick"></div>
      <div class="grid" id="v-grid"></div>
    </div>
  </div>

  <div class="controls">
    <button class="primary" id="btn-play">开吵 ▶</button>
    <button id="btn-speed">速度 1.0×</button>
    <button id="btn-reset">重来</button>
    <div class="hint">空格键 = 开吵 / 暂停　·　数据来自你的券包、麦险分与营养表</div>
  </div>
</div>

<script>
const SCRIPT = __SCRIPT__;
const SUMMARY = __SUMMARY__;

const NAMES = {};
document.querySelectorAll(".actor").forEach(el => {
  NAMES[el.dataset.id] = el.querySelector(".name").textContent;
  el.dataset.base = el.querySelector(".name").textContent;
});

const bubble = document.getElementById("bubble");
const whoEl = document.getElementById("who");
const textEl = document.getElementById("text");
const logEl = document.getElementById("log");
const verdict = document.getElementById("verdict");
const stage = document.getElementById("stage");
const btnPlay = document.getElementById("btn-play");
const btnSpeed = document.getElementById("btn-speed");

let idx = 0, playing = false, timer = null, speed = 1.0, typer = null;

function activate(id, mood){
  document.querySelectorAll(".actor").forEach(el=>{
    const on = el.dataset.id === id;
    el.classList.toggle("on", on);
    el.dataset.mood = on ? (mood || "idle") : "idle";
  });
  const talking = !!id;
  bubble.classList.toggle("talking", talking);
  if(!talking) return;
  // 让气泡尖角跟着说话的人走
  const el = document.querySelector('.actor[data-id="' + id + '"]');
  if(!el) return;
  const b = bubble.getBoundingClientRect(), r = el.getBoundingClientRect();
  const x = r.left + r.width / 2 - b.left;
  bubble.style.setProperty("--tail", Math.max(26, Math.min(b.width - 26, x)) + "px");
}

function typeLine(line, done){
  const chars = Array.from(line.text);
  let i = 0;
  const tag = NAMES[line.who] || line.who;
  whoEl.textContent = tag;
  whoEl.style.color = SUMMARY.colorOf[line.who] || "#DA291C";
  textEl.innerHTML = "";
  clearInterval(typer);
  typer = setInterval(()=>{
    i += 1;
    textEl.innerHTML = chars.slice(0, i).join("") + '<span class="cursor"></span>';
    if(i >= chars.length){
      clearInterval(typer);
      textEl.innerHTML = chars.join("");
      done && done();
    }
  }, Math.max(16, 42 / speed));
}

function pushLog(line){
  const row = document.createElement("div");
  row.className = "row";
  const b = document.createElement("b");
  b.textContent = NAMES[line.who] || line.who;
  b.style.color = SUMMARY.accentOf[line.who] || "#FFC72C";
  row.appendChild(b);
  row.appendChild(document.createTextNode(line.text));
  logEl.appendChild(row);
  logEl.scrollTop = logEl.scrollHeight;
}

function step(){
  if(idx >= SCRIPT.length){ finish(); return; }
  const line = SCRIPT[idx];
  activate(line.who, line.mood);
  typeLine(line, ()=>{
    pushLog(line);
    idx += 1;
    if(idx < SCRIPT.length){
      timer = setTimeout(step, 320 / speed);
    } else {
      timer = setTimeout(finish, 500 / speed);
    }
  });
}

function start(){
  reset();
  playing = true;
  btnPlay.textContent = "暂停 ⏸";
  step();
}

function pause(){
  playing = false;
  clearTimeout(timer); clearInterval(typer);
  btnPlay.textContent = "继续 ▶";
}

function reset(){
  clearTimeout(timer); clearInterval(typer);
  idx = 0; playing = false;
  logEl.innerHTML = "";
  verdict.classList.remove("show");
  textEl.innerHTML = '点「开吵」，五个人会为了你这一单当场吵起来。<span class="cursor"></span>';
  whoEl.textContent = "准备开场";
  activate(null, "idle");
  btnPlay.textContent = "开吵 ▶";
}

function finish(){
  playing = false;
  btnPlay.textContent = "再来一遍 ▶";
  activate(null, "idle");
  if(!SUMMARY.winner || !SUMMARY.winner.name) return;
  document.getElementById("v-pick").textContent = SUMMARY.winner.name + "　¥" + SUMMARY.winner.price;
  const grid = document.getElementById("v-grid");
  grid.innerHTML = "";
  (SUMMARY.facts || []).forEach(f=>{
    const d = document.createElement("div");
    d.innerHTML = "<span>" + f.label + "</span>" + f.value;
    grid.appendChild(d);
  });
  verdict.classList.add("show");
  confetti();
}

function confetti(){
  const colors = ["#DA291C","#FFC72C","#ffffff","#7B5BFF"];
  for(let i=0;i<26;i++){
    const c = document.createElement("i");
    c.className = "confetti";
    c.style.left = (8 + Math.random()*84) + "%";
    c.style.top = "-12px";
    c.style.background = colors[i % colors.length];
    c.style.animationDelay = (Math.random()*0.5) + "s";
    stage.appendChild(c);
    setTimeout(()=>c.remove(), 2400);
  }
}

btnPlay.addEventListener("click", ()=>{
  if(playing){ pause(); }
  else if(idx >= SCRIPT.length){ start(); }
  else { playing = true; btnPlay.textContent = "暂停 ⏸"; step(); }
});
document.getElementById("btn-reset").addEventListener("click", reset);
btnSpeed.addEventListener("click", ()=>{
  speed = speed >= 2.4 ? 1.0 : Math.min(2.4, speed + 0.7);
  btnSpeed.textContent = "速度 " + speed.toFixed(1) + "×";
});
document.addEventListener("keydown", e=>{
  if(e.code === "Space"){ e.preventDefault(); btnPlay.click(); }
});

setTimeout(()=>{ if(idx === 0) start(); }, 700);
</script>
</body>
</html>
"""


def render_html(ctx):
    script = ctx.get("_script") or build_script(ctx)
    winner = ctx.get("winner") or {}
    credit = ctx.get("credit") or {}
    facts = []
    if winner.get("kcal"):
        facts.append({"label": "能量", "value": "%s kcal" % winner["kcal"]})
    if winner.get("health") is not None:
        facts.append({"label": "健康分", "value": "%s / 100" % winner["health"]})
    if ctx.get("saved") is not None:
        facts.append({"label": "相对原价", "value": "省 ¥%s" % _fmt_money(ctx["saved"])})
    if credit.get("score"):
        facts.append({"label": "麦险分", "value": "%s（%s）" % (credit["score"], credit.get("tier", ""))})

    summary = {
        "winner": {
            "name": winner.get("name", ""),
            "price": _fmt_money(winner.get("price")),
        },
        "facts": facts,
        "colorOf": {c["id"]: c.get("ink", c["color"]) for c in CAST},
        "accentOf": {c["id"]: c["accent"] for c in CAST},
    }

    title = html.escape(ctx.get("title") or "麦当劳自助管家")
    out = _HTML
    out = out.replace("__TITLE__", title)
    out = out.replace("__CAST__", _cast_html())
    out = out.replace("__SCRIPT__", json.dumps(script, ensure_ascii=False))
    out = out.replace("__SUMMARY__", json.dumps(summary, ensure_ascii=False))
    out = out.replace("__SCORE__", str(credit.get("score") or "—"))
    out = out.replace("__TIER__", html.escape(str(credit.get("tier") or "—")))
    out = out.replace("__LIMIT__", str(credit.get("limit") or "—"))
    return out
