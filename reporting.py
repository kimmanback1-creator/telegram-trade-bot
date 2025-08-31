# reporting.py
import os
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
from io import BytesIO
from supabase import create_client
from telegram import InputFile
import numpy as np

# ====== 환경 변수 ======
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")
supabase = create_client(url, key)

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
    now = datetime.utcnow()
    if period == "week":
        start = now - timedelta(days=7)
    elif period == "month":
        start = now - timedelta(days=30)
    else:
        start = None

    # scalping
    query = supabase.table("scalping_trades").select("user_id,pnl_pct,side")
    if start: query = query.gte("created_at", start.isoformat())
    scalping = query.execute().data

    # swing
    query = supabase.table("swing_trades").select("user_id,pnl_pct,side")
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
        ranking.append((uid, total, avg, len(pnls)))

    ranking.sort(key=lambda x: x[1], reverse=True)  # 누적 손익률 순
    return ranking[:top_n]

# ====== 그래프 생성 ======
def generate_charts(all_trades):
    pnls = [row["pnl_pct"] for row in all_trades if row["pnl_pct"] is not None]
    cum_pnls = np.cumsum(pnls)

    plt.figure(figsize=(6,4))
    plt.plot(range(len(cum_pnls)), cum_pnls, marker="o")
    plt.title("누적 손익률 추이")
    plt.xlabel("거래")
    plt.ylabel("누적 손익률 %")
    plt.grid(True, linestyle="--", alpha=0.7)

    buf = BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    plt.close()
    return buf

# ====== 메시지 포맷 ======
def format_message(period, stats_scalp, stats_swing, stats_total, ranking):
    msg = f"📊 <b>{period.upper()} 리포트</b>\n\n"
    msg += f"📓 단타: {stats_scalp['count']}건, 승률 {stats_scalp['win_rate']:.1f}%, 누적 {stats_scalp['total']:.1f}%\n"
    msg += f"🕰 장기: {stats_swing['count']}건, 승률 {stats_swing['win_rate']:.1f}%, 누적 {stats_swing['total']:.1f}%\n"
    msg += f"📊 전체: {stats_total['count']}건, 승률 {stats_total['win_rate']:.1f}%, 누적 {stats_total['total']:.1f}%\n\n"
    msg += "🏆 랭킹:\n"
    for i, (uid, total, avg, cnt) in enumerate(ranking, 1):
        msg += f"{i}. 유저 {uid} → {total:.1f}% (평균손익률{avg:.1f}%, {cnt}건)\n"
    return msg

# ====== 리포트 전송 ======
async def send_report(bot, period="week"):
    scalping, swing = fetch_trades(period)
    all_trades = scalping + swing

    stats_scalp = calc_stats([t["pnl_pct"] for t in scalping if t["pnl_pct"] is not None])
    stats_swing = calc_stats([t["pnl_pct"] for t in swing if t["pnl_pct"] is not None])
    stats_total = calc_stats([t["pnl_pct"] for t in all_trades if t["pnl_pct"] is not None])


    ranking = calc_ranking(all_trades, top_n=3 if period=="week" else 5)

    msg = format_message(period, stats_scalp, stats_swing, stats_total, ranking)

    # 그래프
    chart = generate_charts(all_trades)

    await bot.send_message(CHANNEL_ID, msg, parse_mode="HTML")
    await bot.send_photo(CHANNEL_ID, InputFile(chart, filename="report.png"))

