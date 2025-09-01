# reporting.py
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import matplotlib.pyplot as plt
from matplotlib import font_manager, rcParams
from io import BytesIO
from supabase import create_client
from telegram import InputFile
import numpy as np
from collections import defaultdict

# ====== 환경 변수 ======
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")
supabase = create_client(url, key)

# ====== 폰트 적용 ======
font_path = "font/NanumGothic-Regular.ttf"  
if os.path.exists(font_path):
    font_prop = font_manager.FontProperties(fname=font_path)
    rcParams['font.family'] = font_prop.get_name()   
    rcParams['axes.unicode_minus'] = False
    print(f"[INFO] Matplotlib font set to: {font_prop.get_name()}")
else:
    print(f"[WARN] Font file not found at {font_path}")


# ====== 통계 계산 ======
def calc_stats(profits):
    if not profits:
        return {"count": 0, "win": 0, "lose": 0, "win_rate": 0,
                "total": 0, "avg": 0, "pf": 0, "pf_eval": "N/A"}
    
    total = len(profits)
    win = len([p for p in profits if p > 0])
    lose = len([p for p in profits if p < 0])
    gross_profit = sum([p for p in profits if p > 0])
    gross_loss = abs(sum([p for p in profits if p < 0]))
    win_rate = win / total * 100
    avg = sum(profits) / total
    total_profit = sum(profits)
    pf = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    if pf == float("inf"):
        pf_eval = "∞ 무손실"
    elif pf >= 2:
        pf_eval = "✅ 양호"
    elif pf >= 1:
        pf_eval = "⚠️ 보통"
    else:
        pf_eval = "❌ 위험"

    return {"count": total, "win": win, "lose": lose, "win_rate": win_rate,
            "total": total_profit, "avg": avg, "pf": pf, "pf_eval": pf_eval}

# ====== 데이터 조회 ======
def fetch_trades(period="week"):
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    
    if period == "week":
        start = now - timedelta(days=7)
    elif period == "month":
        start = now - timedelta(days=30)
    else:
        start = None

    if start:
        start_utc = start.astimezone(ZoneInfo("UTC"))
    else:
        start_utc = None

    # scalping
    query = supabase.table("scalping_trades").select("user_id,pnl_pct,side,symbol")
    if start: query = query.gte("created_at", start.isoformat())
    scalping = query.execute().data

    # swing
    query = supabase.table("swing_trades").select("user_id,pnl_pct,side,symbol")
    if start: query = query.gte("date_closed", start.isoformat())
    swing = query.execute().data

    return scalping, swing

# ====== 랭킹 계산 ======
def calc_ranking(all_trades, top_n=3):
    user_stats = {}
    for row in all_trades:
        uid = row["user_id"]
        pnl = row["pnl_pct"] if row["pnl_pct"] is not None else 0
        user_stats.setdefault(uid, []).append(pnl)

    ranking = []
    for uid, pnls in user_stats.items():
        total = sum(pnls)
        avg = total / len(pnls)
        alias_resp = supabase.table("user_alias").select("alias").eq("user_id", uid).execute()
        if alias_resp.data and len(alias_resp.data) > 0:
            alias = alias_resp.data[0]["alias"]
        else:
            alias = f"유저{uid}"  # fallback
        ranking.append((alias, total, avg, len(pnls)))

    ranking.sort(key=lambda x: x[1], reverse=True)  # 누적 손익률 순
    return ranking[:top_n]

# ====== 종목별 승률 계산 ======
def calc_symbol_stats(all_trades, top_n=3):
    stats = defaultdict(lambda: {"win":0, "total":0})
    for t in all_trades:
        if t.get("pnl_pct") is None: 
            continue
        sym = t.get("symbol", "N/A")
        stats[sym]["total"] += 1
        if t["pnl_pct"] > 0:
            stats[sym]["win"] += 1
    
    results = []
    for sym, d in stats.items():
        win_rate = d["win"] / d["total"] * 100
        results.append((sym, win_rate, d["total"]))
    
    results.sort(key=lambda x: x[1], reverse=True)
    return results[:top_n], list(stats.keys())
    
# ====== 그래프 생성 ======
def generate_charts(all_trades):
    pnls = [row["pnl_pct"] for row in all_trades if row["pnl_pct"] is not None]
    cum_pnls = np.cumsum(pnls)

    plt.figure(figsize=(6,4))
    plt.plot(range(len(cum_pnls)), cum_pnls, marker="o")
    plt.title("PNL 추이")
    plt.xlabel("거래")
    plt.ylabel("누적 PNL %")
    plt.grid(True, linestyle="--", alpha=0.7)

    buf = BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    plt.close()
    return buf

# ====== 메시지 포맷 ======
def format_message(period, stats_scalp, stats_swing, stats_total, ranking, all_trades):
    msg = f"📊 <b>{period.upper()} 리포트</b>\n\n"
    msg += "전체 사용자 통계\n"
    msg += "────────────────────────\n"
    msg += f"단타: {stats_scalp['count']}건, 승률 {stats_scalp['win_rate']:.1f}%, PNL {stats_scalp['total']:.1f}%\n"
    msg += f"장기: {stats_swing['count']}건, 승률 {stats_swing['win_rate']:.1f}%, PNL {stats_swing['total']:.1f}%\n"
    msg += f"전체: {stats_total['count']}건, 승률 {stats_total['win_rate']:.1f}%, PNL {stats_total['total']:.1f}%\n\n"
    msg += f"수익지수(PF): {stats_total['pf']:.2f} {stats_total['pf_eval']}\n\n"
    
    long_cnt = len([t for t in all_trades if t.get("side") == "롱"])
    short_cnt = len([t for t in all_trades if t.get("side") == "숏"])
    total_pos = long_cnt + short_cnt
    long_ratio = (long_cnt/total_pos*100) if total_pos else 0
    short_ratio = (short_cnt/total_pos*100) if total_pos else 0
    
    scalp_cnt = stats_scalp["count"]
    swing_cnt = stats_swing["count"]
    total_style = scalp_cnt + swing_cnt
    scalp_ratio = (scalp_cnt/total_style*100) if total_style else 0
    swing_ratio = (swing_cnt/total_style*100) if total_style else 0
    
    if long_cnt + short_cnt > 0:
        long_ratio = long_cnt / (long_cnt + short_cnt) * 100
        short_ratio = short_cnt / (long_cnt + short_cnt) * 100
    else:
        long_ratio = short_ratio = 0

    if scalp_cnt + swing_cnt > 0:
        scalp_ratio = scalp_cnt / (scalp_cnt + swing_cnt) * 100
        swing_ratio = swing_cnt / (scalp_cnt + swing_cnt) * 100
    else:
        scalp_ratio = swing_ratio = 0

    msg += f"단타/장기 비율 → 단타 {scalp_ratio:.1f}%, 장기 {swing_ratio:.1f}%\n\n"
    msg += f"포지션 비율 → 롱 {long_ratio:.1f}%, 숏 {short_ratio:.1f}%\n"

    top_symbols, all_symbols = calc_symbol_stats(all_trades, top_n=3)
    if all_symbols:
        msg += f"📌 이번주 거래 종목: {', '.join(all_symbols)}\n\n"
    if top_symbols:
        msg += "🥇 승률 TOP3 종목:\n"
        for i, (sym, winr, cnt) in enumerate(top_symbols, 1):
            msg += f"{i}. {sym} – {winr:.1f}% ({cnt}건)\n"
        msg += "\n"
    
    msg += "🏆 랭킹:\n"
    for i, (alias, total, avg, cnt) in enumerate(ranking, 1):
        msg += f"{i}. {alias} → {total:.1f}% (평균손익률 {avg:.1f}%, {cnt}건)\n"
    
    return msg

# ====== 리포트 전송 ======
async def send_report(bot, period="week"):
    scalping, swing = fetch_trades(period)
    all_trades = scalping + swing

    stats_scalp = calc_stats([t["pnl_pct"] for t in scalping if t["pnl_pct"] is not None])
    stats_swing = calc_stats([t["pnl_pct"] for t in swing if t["pnl_pct"] is not None])
    stats_total = calc_stats([t["pnl_pct"] for t in all_trades if t["pnl_pct"] is not None])


    ranking = calc_ranking(all_trades, top_n=3 if period=="week" else 5)

    msg = format_message(period, stats_scalp, stats_swing, stats_total, ranking, all_trades)

    # 그래프
    chart = generate_charts(all_trades)

    await bot.send_message(CHANNEL_ID, msg, parse_mode="HTML")
    await bot.send_photo(CHANNEL_ID, InputFile(chart, filename="report.png"))









