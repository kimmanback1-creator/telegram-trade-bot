import os
from fastapi import FastAPI, Request
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, CallbackQueryHandler
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from supabase import create_client
from fastapi.responses import JSONResponse
from reporting import send_report
import random
import httpx

TOKEN = os.getenv("BOT_TOKEN")
url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")
COINGECKO_API = "https://api.coingecko.com/api/v3"
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

supabase = create_client(url, key)
telegram_app = Application.builder().token(TOKEN).build()
app = FastAPI()

@app.api_route("/", methods=["GET", "HEAD"])
async def root():
    return {"status": "ok"}
    
# ====== 별칭 생성기 ======
ADJECTIVES = ["불타는", "날쌘", "예리한", "강인한", "차가운", "뜨거운", "빠른", "은밀한", "화끈한", "거대한", "섹시한", "냉정한", "영리한", "잔혹한", "고독한", "거친", "맹렬한", "전설의 ", "저주받은", "깜찍한", "엉뚱한", "상큼한", "도도한", "노련한"]
ANIMALS = ["곰", "호랑이", "코브라", "매", "황소", "늑대", "독수리", "상어", "팬더", "사자", "부엉이", "고양이", "아깽이", "강아지", "개미", "불개미", "벌꿀오소리", "얼룩말", "캥거루", "침팬치", "여우", "고래", "돌고래", "해파리", "펭귄", "물개", "까마귀", "앵무새", "공작새", "참새", "악어", "도마뱀", "개구리", "장수말벌", "풍뎅이"]

def generate_alias(user_id: int) -> str:
    last4 = str(user_id)[-4:]
    adj = random.choice(ADJECTIVES)
    animal = random.choice(ANIMALS)
    return f"{adj}{animal}-{last4}"
    
def get_or_create_alias(user_id: int):
    response = supabase.table("user_alias").select("alias").eq("user_id", user_id).execute()
    if response.data:
        return response.data[0]["alias"]

    alias = generate_alias(user_id)
    supabase.table("user_alias").insert({"user_id": user_id, "alias": alias}).execute()
    return alias

#전역 변수
MAIN_MENU = [["📓 일지작성(단타)", "일지작성(장기)"], ["📊 통계보기", "❌ 취소"]]
LONG_MENU = [["새 진입 기록", "청산하기"], ["❌ 취소 / 뒤로가기"]]

# 단계 정의
IMAGE, SYMBOL, SIDE, LEVERAGE, PNL, REASON = range(6)
L_IMAGE, L_SYMBOL, L_SIDE, L_LEVERAGE, L_ENTRY_PRICE, L_REASON_ENTRY = range(6, 12)  # 장기 진입
L_MENU, L_SELECT_TRADE, L_EXIT_PRICE, L_PNL, L_REASON_EXIT = range(12, 17)  # 장기 청산

def safe_supabase_call(query):
    try:
        return query.execute()
    except Exception as e:
        print("❌ Supabase error:", e)
        return None

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
        
    print("Chat ID:", update.effective_chat.id)
    user_id = update.effective_user.id
    alias = get_or_create_alias(user_id)
    reply_markup = ReplyKeyboardMarkup(MAIN_MENU, resize_keyboard=True)
    await update.message.reply_text(f"환영합니다! 매매일지 봇입니다!\n"f"👉 당신의 고유 별칭 <b>{alias}</b> 입니다.\n"f"리포트에서 동일한 별칭으로 표시됩니다.", reply_markup=reply_markup, parse_mode="HTML")

# 단타 기록 시작
async def scalping_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"👤 {update.effective_user.id} -> 단타 일지작성 시작")
    chat_id = update.effective_chat.id
    try:
        await context.bot.delete_message(chat_id, update.message.message_id)
    except:
        pass
    
    # 안내 메시지도 리스트에 쌓기 위한 초기화
    context.user_data["bot_msgs"] = []
    msg = await update.message.reply_text("📷 먼저 진입 차트 이미지를 업로드해주세요.")
    context.user_data["bot_msgs"].append(msg.message_id)
    return IMAGE


async def get_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        msg = await update.message.reply_text("이미지를 업로드해주세요.")
        context.user_data["bot_msgs"].append(msg.message_id)
        return IMAGE
    context.user_data["user_image_id"] = update.message.message_id
    
    photo = update.message.photo[-1]  # 가장 큰 해상도 선택
    context.user_data["image_id"] = photo.file_id

    msg = await update.message.reply_text("종목을 입력하세요 (예: BTC)")
    context.user_data["bot_msgs"].append(msg.message_id)
    return SYMBOL


async def get_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["symbol"] = update.message.text

    # 유저 입력 메시지 삭제
    await context.bot.delete_message(update.effective_chat.id, update.message.message_id)

    msg = await update.message.reply_text("포지션을 입력하세요 (롱/숏)")
    context.user_data["bot_msgs"].append(msg.message_id)
    return SIDE


async def get_side(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["side"] = update.message.text
    await context.bot.delete_message(update.effective_chat.id, update.message.message_id)

    msg = await update.message.reply_text("배율을 입력하세요 (예: 1, 3, 5)")
    context.user_data["bot_msgs"].append(msg.message_id)
    return LEVERAGE


async def get_leverage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        leverage = float(text)
        if leverage <= 0:
            raise ValueError
    except ValueError:
        msg = await update.message.reply_text("❌ 배율은 0보다 큰 숫자로 입력해주세요 (예: 1, 3, 5)")
        context.user_data["bot_msgs"].append(msg.message_id)
        return LEVERAGE

    context.user_data["leverage"] = leverage
    await context.bot.delete_message(update.effective_chat.id, update.message.message_id)

    msg = await update.message.reply_text("최종 결과 수익률/손실률(%)을 입력하세요 (예: 12, -5)")
    context.user_data["bot_msgs"].append(msg.message_id)
    return PNL


async def get_pnl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        pnl_pct = float(text)
    except ValueError:
        msg = await update.message.reply_text("❌ 수익률/손실률은 숫자로 입력해주세요 (예: 12, -5)")
        context.user_data["bot_msgs"].append(msg.message_id)
        return PNL

    context.user_data["pnl_pct"] = pnl_pct
    await context.bot.delete_message(update.effective_chat.id, update.message.message_id)

    msg = await update.message.reply_text("진입 근거를 입력하세요")
    context.user_data["bot_msgs"].append(msg.message_id)
    return REASON


async def get_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["reason"] = update.message.text
    await context.bot.delete_message(update.effective_chat.id, update.message.message_id)

    user_id = update.message.from_user.id
    image_id = context.user_data["image_id"]
    symbol = context.user_data["symbol"]
    side = context.user_data["side"]
    leverage = context.user_data["leverage"]
    pnl_pct = context.user_data["pnl_pct"]
    reason = context.user_data["reason"]
    date_now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # DB 저장 supabase
    supabase.table("scalping_trades").insert({
    "user_id": user_id,
    "image_id": image_id,
    "symbol": symbol,
    "side": side,
    "leverage": leverage,
    "pnl_pct": pnl_pct,
    "reason": reason
}).execute()

    # 🔥 지금까지 봇이 보낸 안내 메시지들 싹 삭제
    for msg_id in context.user_data.get("bot_msgs", []):
        try:
            await context.bot.delete_message(update.effective_chat.id, msg_id)
        except:
            pass
    context.user_data["bot_msgs"] = []  # 초기화
    
    if "user_image_id" in context.user_data:
        try:
            await context.bot.delete_message(update.effective_chat.id, context.user_data["user_image_id"])
        except:
            pass

    # 최종 메시지 (이미지 + 요약)만 남김
    await update.message.reply_photo(
        photo=image_id,
        caption=(
            f"📓 [매매일지]\n"
            f"- 날짜: {date_now}\n"
            f"- 종목: {symbol}\n"
            f"- 포지션: {side}\n"
            f"- 배율: {leverage}x\n"
            f"- 결과: {pnl_pct}%\n"
            f"- 진입 근거: \"{reason}\""
        )
    )
    return ConversationHandler.END

# =========================
# 통계보기 유틸 함수
# =========================
def calc_stats(profits):
    if not profits:
        return {
            "count": 0, "win": 0, "lose": 0, "win_rate": 0,
            "total": 0, "avg": 0, "pf": 0, "pf_eval": "N/A"
        }
    
    total_trades = len(profits)
    win_trades = len([p for p in profits if p > 0])
    lose_trades = len([p for p in profits if p < 0])
    gross_profit = sum([p for p in profits if p > 0])
    gross_loss = abs(sum([p for p in profits if p < 0]))
    total_profit = sum(profits)
    avg_profit = total_profit / total_trades
    win_rate = win_trades / total_trades * 100
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    if profit_factor == float("inf"):
        pf_eval = "∞ 무손실"
    elif profit_factor >= 2:
        pf_eval = "✅ 양호"
    elif profit_factor >= 1:
        pf_eval = "⚠️ 보통"
    else:
        pf_eval = "❌ 위험"

    return {
        "count": total_trades, "win": win_trades, "lose": lose_trades,
        "win_rate": win_rate, "total": total_profit, "avg": avg_profit,
        "pf": profit_factor, "pf_eval": pf_eval
    }

# =========================
# 통계보기 유틸 함수
# =========================
def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
    
# =========================
# 통계보기
# =========================
async def show_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"👤 {update.effective_user.id} -> 통계보기 요청")
    chat_id = update.effective_chat.id
    try:
        await context.bot.delete_message(chat_id, update.message.message_id)
    except:
        pass
    
    user_id = update.message.from_user.id

    # 단타
    response = safe_supabase_call(
        supabase.table("scalping_trades").select("pnl_pct").eq("user_id", user_id)
    )
    scalping_profits = [safe_float(row["pnl_pct"]) for row in response.data if row["pnl_pct"] is not None] if response else []
    scalping_profits = [p for p in scalping_profits if p is not None]

    response = safe_supabase_call(
        supabase.table("swing_trades").select("pnl_pct, exit_price").eq("user_id", user_id)
    )
    swing_profits = [safe_float(row["pnl_pct"]) for row in response.data if row["pnl_pct"] is not None] if response else []
    swing_profits = [p for p in swing_profits if p is not None]

    closed_trades = sum(1 for row in (response.data if response else []) if row["exit_price"] is not None)
    open_trades   = sum(1 for row in (response.data if response else []) if row["exit_price"] is None)

    # 전체
    all_profits = scalping_profits + swing_profits

    if not all_profits:
        await update.message.reply_text("📊 기록이 없습니다.")
        return

    # 통계 계산
    stats_scalp = calc_stats(scalping_profits)
    stats_swing = calc_stats(swing_profits)
    stats_total = calc_stats(all_profits)

    # 출력 메시지
    stats_message = (
        f"📊 <b>매매 통계</b>\n\n"

        f"📓 <b>단타 거래</b>\n"
        f"- 총 거래 수: {stats_scalp['count']}\n"
        f"- 승리: {stats_scalp['win']} | 패배: {stats_scalp['lose']}\n"
        f"- 승률: {stats_scalp['win_rate']:.2f}%\n"
        f"- 누적 손익률: {stats_scalp['total']:.2f}%\n"
        f"- 거래당 평균 수익률: {stats_scalp['avg']:.2f}%\n"
        f"- 수익지수: {stats_scalp['pf']:.2f} → {stats_scalp['pf_eval']}\n\n"

        f"🕰 <b>장기 거래</b>\n"
        f"- 총 거래 수: {stats_swing['count']}\n"
        f"- 청산된 거래: {closed_trades} | 미청산 거래: {open_trades}\n"
        f"- 승리: {stats_swing['win']} | 패배: {stats_swing['lose']}\n"
        f"- 승률: {stats_swing['win_rate']:.2f}%\n"
        f"- 누적 손익률: {stats_swing['total']:.2f}%\n"
        f"- 거래당 평균 수익률: {stats_swing['avg']:.2f}%\n"
        f"- 수익지수: {stats_swing['pf']:.2f} → {stats_swing['pf_eval']}\n\n"

        f"📊 <b>전체 합산</b>\n"
        f"- 총 거래 수: {stats_total['count']}\n"
        f"- 승리: {stats_total['win']} | 패배: {stats_total['lose']}\n"
        f"- 승률: {stats_total['win_rate']:.2f}%\n"
        f"- 누적 손익률: {stats_total['total']:.2f}%\n"
        f"- 거래당 평균 수익률: {stats_total['avg']:.2f}%\n"
        f"- 수익지수: {stats_total['pf']:.2f} → {stats_total['pf_eval']}"
    )

    await update.message.reply_text(stats_message, parse_mode="HTML")
    return ConversationHandler.END
# =========================
# 장기 매매일지
# =========================
async def swing_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("🔥 swing_start 진입됨:", update.message.text)
    chat_id = update.effective_chat.id
    try:
        await context.bot.delete_message(chat_id, update.message.message_id)
    except:
        pass
    
    reply_markup = ReplyKeyboardMarkup(LONG_MENU, resize_keyboard=True)
    context.user_data["bot_msgs"] = []
    msg = await update.message.reply_text("🕰 장기 매매일지: 무엇을 하시겠습니까?", reply_markup=reply_markup)
    context.user_data["bot_msgs"].append(msg.message_id)
    print("swing_start finished, moved to L_MENU")
    return L_MENU

# 장기 - 진입
async def get_l_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"👤 {update.effective_user.id} -> 장기 새 진입 시작 (이미지 대기)")
    chat_id = update.effective_chat.id
    try:
        await context.bot.delete_message(chat_id, update.message.message_id)
    except:
        pass
    
    if not update.message.photo:
        print("⚠️ get_l_image: No photo found, asking again")
        msg = await update.message.reply_text("이미지를 업로드해주세요.")
        context.user_data.setdefault("bot_msgs", []).append(msg.message_id)   
        return L_IMAGE

    context.user_data["user_image_id"] = update.message.message_id
    
    photo = update.message.photo[-1]
    context.user_data["image_id"] = photo.file_id
    print("✅ get_l_image: photo stored", context.user_data["image_id"])
    
    msg = await update.message.reply_text("종목을 입력하세요 (예: BTC)")
    context.user_data.setdefault("bot_msgs", []).append(msg.message_id)       
    return L_SYMBOL


async def get_l_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("🚀 get_l_symbol triggered:", update.message.text)
    context.user_data["symbol"] = update.message.text   
    
    # 유저메세지 삭제
    await context.bot.delete_message(update.effective_chat.id, update.message.message_id)
    
    msg = await update.message.reply_text("포지션을 입력하세요 (롱/숏)")
    context.user_data.setdefault("bot_msgs", []).append(msg.message_id)       
    return L_SIDE


async def get_l_side(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("🚀 get_l_side triggered:", update.message.text)
    context.user_data["side"] = update.message.text     
    await context.bot.delete_message(update.effective_chat.id, update.message.message_id)
    
    msg = await update.message.reply_text("배율을 입력하세요 (예: 1, 3, 5)")
    context.user_data.setdefault("bot_msgs", []).append(msg.message_id)       
    return L_LEVERAGE


async def get_l_leverage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("🚀 get_l_leverage triggered:", update.message.text)
    text = update.message.text.strip()
    try:
        leverage = float(text)
        if leverage <= 0:
            raise ValueError
    except ValueError:
        msg = await update.message.reply_text("❌ 배율은 0보다 큰 숫자로 입력해주세요 (예: 1, 3, 5)")
        context.user_data.setdefault("bot_msgs", []).append(msg.message_id)
        return L_LEVERAGE
    context.user_data["leverage"] = leverage
    await context.bot.delete_message(update.effective_chat.id, update.message.message_id)
    
    msg = await update.message.reply_text("진입가를 입력하세요 (예: 24500)")
    context.user_data.setdefault("bot_msgs", []).append(msg.message_id)       
    return L_ENTRY_PRICE


async def get_l_entry_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("🚀 get_l_entry_price triggered:", update.message.text)
    text = update.message.text.strip()
    try:
        entry_price = float(text)
        if entry_price <= 0:
            raise ValueError
    except ValueError:
        msg = await update.message.reply_text("❌ 진입가는 0보다 큰 숫자로 입력해주세요 (예: 24500)")
        context.user_data["bot_msgs"].append(msg.message_id)
        return L_ENTRY_PRICE

    context.user_data["entry_price"] = entry_price
    await context.bot.delete_message(update.effective_chat.id, update.message.message_id)

    msg = await update.message.reply_text("진입 근거를 입력하세요")
    context.user_data["bot_msgs"].append(msg.message_id)
    return L_REASON_ENTRY


async def get_l_reason_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("🚀 get_l_reason_entry triggered:", update.message.text)
    context.user_data["reason_entry"] = update.message.text   
    await context.bot.delete_message(update.effective_chat.id, update.message.message_id)

    user_id = update.message.from_user.id
    image_id = context.user_data["image_id"]
    symbol = context.user_data["symbol"]
    side = context.user_data["side"]
    leverage = context.user_data["leverage"]
    entry_price = context.user_data["entry_price"]
    reason_entry = context.user_data["reason_entry"]
    date_now = datetime.now().strftime("%Y-%m-%d %H:%M")

    #DB
    safe_supabase_call(
        supabase.table("swing_trades").insert({
            "user_id": user_id,
            "image_id": image_id,
            "symbol": symbol,
            "side": side,
            "leverage": leverage,
            "entry_price": entry_price,
            "reason_entry": reason_entry
    })
)


    for msg_id in context.user_data.get("bot_msgs", []):
        try:
            await context.bot.delete_message(update.effective_chat.id, msg_id)
        except:
            pass
    context.user_data["bot_msgs"] = []
    
    if "user_image_id" in context.user_data:
        try:
            await context.bot.delete_message(update.effective_chat.id, context.user_data["user_image_id"])
        except:
            pass

    await update.message.reply_photo(
        photo=image_id,
        caption=(f"🕰 [장기 매매일지 - 진입]\n"
                 f"- 날짜: {date_now}\n"
                 f"- 종목: {symbol}\n"
                 f"- 포지션: {side}\n"
                 f"- 배율: {leverage}x\n"
                 f"- 진입가: {entry_price}\n"
                 f"- 진입 근거: \"{reason_entry}\"")
    )   
    reply_markup = ReplyKeyboardMarkup(LONG_MENU, resize_keyboard=True)
    await update.message.reply_text("기록완료", reply_markup=reply_markup)
    return L_MENU




# 장기 - 청산 (버튼 방식)
async def swing_show_open_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"👤 {update.effective_user.id} -> 장기 청산하기 버튼 클릭")
    chat_id = update.effective_chat.id
    try:
        await context.bot.delete_message(chat_id, update.message.message_id)
    except:
        pass
    
    response = supabase.table("swing_trades").select("trade_id, symbol, side, entry_price").is_("exit_price", None).execute()
    rows = response.data

    if not rows:
        msg = await update.message.reply_text("📭 현재 열린 포지션이 없습니다.")
        context.user_data.setdefault("bot_msgs", []).append(msg.message_id)
        return ConversationHandler.END

    keyboard = [
    [InlineKeyboardButton(
        f"{row.get('symbol', 'N/A')} {row.get('side', 'N/A')} @ {row.get('entry_price', '0')}",
        callback_data=str(row['trade_id'])
    )]
    for row in rows
]

    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = await update.message.reply_text("📑 [열린 포지션 목록]\n청산할 포지션을 선택하세요:", reply_markup=reply_markup)
    context.user_data.setdefault("bot_msgs", []).append(msg.message_id)
    return L_SELECT_TRADE


# 버튼 클릭 → 청산할 포지션 선택
async def swing_select_trade_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    trade_id = int(query.data)
    context.user_data["close_id"] = trade_id

    msg = await query.edit_message_text(
        f"선택한 포지션 ID: {trade_id}\n청산가를 입력하세요 (예: 27000)"
    )
    context.user_data.setdefault("bot_msgs", []).append(msg.message_id)
    return L_EXIT_PRICE


# 청산가 입력
async def swing_exit_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        exit_price = float(text)
        if exit_price <= 0:
            raise ValueError
    except ValueError:
        msg = await update.message.reply_text("❌ 청산가는 0보다 큰 숫자로 입력해주세요 (예: 27000)")
        context.user_data.setdefault("bot_msgs", []).append(msg.message_id)
        return L_EXIT_PRICE

    context.user_data["exit_price"] = exit_price
    context.user_data.setdefault("user_msgs", []).append(update.message.message_id)

    msg = await update.message.reply_text("청산 근거를 입력하세요")
    context.user_data.setdefault("bot_msgs", []).append(msg.message_id)
    return L_REASON_EXIT


# 청산 근거 입력 → 최종 처리
async def swing_reason_exit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    trade_id = context.user_data["close_id"]
    exit_price = context.user_data["exit_price"]
    reason_exit = update.message.text

    context.user_data.setdefault("user_msgs", []).append(update.message.message_id)

    # DB에서 entry_price, side, leverage 가져오기
    response = supabase.table("swing_trades").select("entry_price, side, leverage").eq("trade_id", trade_id).execute()
    
    if not response.data:   
        await update.message.reply_text("❌ 해당 포지션 정보를 찾을 수 없습니다.")
        return ConversationHandler.END
    
    row = response.data[0]
    entry_price = float(row["entry_price"]) 
    side = row["side"]
    leverage = float(row["leverage"])


    # 자동 PnL 계산
    if side == "롱":
        pnl_pct = ((exit_price - entry_price) / entry_price) * 100 * float(leverage)
    else:  # 숏
        pnl_pct = ((entry_price - exit_price) / entry_price) * 100 * float(leverage)
    pnl_pct = round(pnl_pct, 2)

    # DB 업데이트
    supabase.table("swing_trades").update({
    "exit_price": exit_price,
    "pnl_pct": pnl_pct,
    "reason_exit": reason_exit,
    "date_closed": datetime.now().isoformat()
}).eq("trade_id", trade_id).execute()

    # 불필요 메시지 삭제
    chat_id = update.effective_chat.id
    for msg_id in context.user_data.get("bot_msgs", []):
        try:
            await context.bot.delete_message(chat_id, msg_id)
        except:
            pass
    for msg_id in context.user_data.get("user_msgs", []):
        try:
            await context.bot.delete_message(chat_id, msg_id)
        except:
            pass
    context.user_data["bot_msgs"] = []
    context.user_data["user_msgs"] = []

    # 최종 메시지 출력
    date_now = datetime.now().strftime("%Y-%m-%d %H:%M")
    await update.message.reply_text(
        f"✅ [장기 매매일지 - 청산 완료]\n"
        f"- 날짜: {date_now}\n"
        f"- ID: {trade_id}\n"
        f"- 진입가: {entry_price}\n"
        f"- 레버리지: {leverage}x\n"
        f"- 청산가: {exit_price}\n"
        f"- 결과: {pnl_pct}%\n"
        f"- 청산 근거: \"{reason_exit}\""
    )

    reply_markup = ReplyKeyboardMarkup(LONG_MENU, resize_keyboard=True)
    await update.message.reply_text("청산완료", reply_markup=reply_markup)
    return L_MENU

# 취소
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        await context.bot.delete_message(chat_id, update.message.message_id)
    except:
        pass
    
    reply_markup = ReplyKeyboardMarkup(MAIN_MENU, resize_keyboard=True)
    await update.message.reply_text("❌ 입력이 취소되었습니다. 메뉴에서 다시 시작하세요.", reply_markup=reply_markup)
    return ConversationHandler.END

cancel_handler = MessageHandler(filters.Text(["❌ 취소", "❌ 취소 / 뒤로가기"]), cancel)


    # 단타 핸들러
conv_scalp = ConversationHandler(
    entry_points=[MessageHandler(filters.Text(["📓 일지작성(단타)"]), scalping_start)],
    states={
        IMAGE: [MessageHandler(filters.PHOTO, get_image)],
        SYMBOL: [cancel_handler, MessageHandler(filters.TEXT & ~filters.COMMAND, get_symbol)],
        SIDE: [cancel_handler, MessageHandler(filters.TEXT & ~filters.COMMAND, get_side)],
        LEVERAGE: [cancel_handler, MessageHandler(filters.TEXT & ~filters.COMMAND, get_leverage)],
        PNL: [cancel_handler, MessageHandler(filters.TEXT & ~filters.COMMAND, get_pnl)],
        REASON: [cancel_handler, MessageHandler(filters.TEXT & ~filters.COMMAND, get_reason)],

    },
    fallbacks=[
        cancel_handler,
        CommandHandler("cancel", cancel)
    ],
)

conv_long = ConversationHandler(
    entry_points=[
        MessageHandler(filters.Text(["일지작성(장기)"]), swing_start),
        MessageHandler(filters.Text(["새 진입 기록"]), get_l_image)
    ],
    states={
        L_MENU: [
            MessageHandler(filters.Text(["새 진입 기록"]), get_l_image),
            MessageHandler(filters.Text(["청산하기"]), swing_show_open_positions),
            cancel_handler
        ],
        L_IMAGE: [cancel_handler, MessageHandler(filters.PHOTO, get_l_image)],
        L_SYMBOL: [cancel_handler, MessageHandler(filters.TEXT & ~filters.COMMAND, get_l_symbol)],
        L_SIDE: [cancel_handler, MessageHandler(filters.TEXT & ~filters.COMMAND, get_l_side)],
        L_LEVERAGE: [cancel_handler, MessageHandler(filters.TEXT & ~filters.COMMAND, get_l_leverage)],
        L_ENTRY_PRICE: [cancel_handler, MessageHandler(filters.TEXT & ~filters.COMMAND, get_l_entry_price)],
        L_REASON_ENTRY: [cancel_handler, MessageHandler(filters.TEXT & ~filters.COMMAND, get_l_reason_entry)],
        L_SELECT_TRADE: [cancel_handler, CallbackQueryHandler(swing_select_trade_callback)],
        L_EXIT_PRICE: [cancel_handler, MessageHandler(filters.TEXT & ~filters.COMMAND, swing_exit_price)],
        L_REASON_EXIT: [cancel_handler, MessageHandler(filters.TEXT & ~filters.COMMAND, swing_reason_exit)],
    },
    fallbacks=[
        cancel_handler,
        CommandHandler("cancel", cancel)
    ],
)

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(MessageHandler(filters.Text(["📊 통계보기"]), show_statistics))
telegram_app.add_handler(conv_scalp)
telegram_app.add_handler(conv_long)

KST = ZoneInfo("Asia/Seoul")

async def safe_send_report(ctx, period):
    try:
        await send_report(ctx.application.bot, period)
        print(f"✅ {period} 리포트 전송 완료")
    except Exception as e:
        print(f"❌ {period} 리포트 실패:", e)

async def weekly_report(ctx):
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[DEBUG] Weekly job triggered at {now} (KST)")
    await safe_send_report(ctx, "week")

async def monthly_report(ctx):
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[DEBUG] Monthly job triggered at {now} (KST)")
    await safe_send_report(ctx, "month")
    
@app.on_event("startup")
async def on_startup():
    await telegram_app.initialize()
    print("✅ Telegram Application initialized")

    job_queue = telegram_app.job_queue
    job_queue.scheduler.configure(timezone=KST)
    await job_queue.start()
    
    job_queue.run_daily(
        weekly_report,
        time=time(hour=22, minute=0, tzinfo=KST),
        days=(0,1,2,3,4,5,6),
        name="weekly_report"   
    )

    
    job_queue.run_monthly(
        monthly_report,
        when=time(hour=22, minute=0, tzinfo=KST),
        day=1,
        name="monthly_report"
    )

    for job in job_queue.jobs():
        aps_job = getattr(job, "aps_job", None)
        if aps_job:
            print(f"[DEBUG] Job registered: {job.name}, next_run_time={aps_job.next_run_time}")
        else:
            print(f"[DEBUG] Job registered: {job.name}, next_run_time=Unknown")

    #await send_report(telegram_app.bot, period="week")
    #await send_report(telegram_app.bot, period="month")
    
@app.on_event("shutdown")
async def on_shutdown():
    await telegram_app.shutdown()
    print("🛑 Telegram Application shutdown")

@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, telegram_app.bot)
        await telegram_app.process_update(update)
        return JSONResponse(content={"ok": True}, status_code=200)
    except Exception as e:
        print("❌ Webhook error:", e)
        return JSONResponse(content={"ok": False, "error": str(e)}, status_code=500)

last_called = {}
last_global_call = None

async def get_top3_tokens(category_id: str):
    global last_global_call
    now = datetime.now()
    
    if category_id in last_called and (now - last_called[category_id]).seconds < 60:
        print(f"[Skip] {category_id} 최근 호출 있음 → API 호출 생략")
        return []

    if last_global_call and (now - last_global_call).seconds < 1:
        wait_time = 1 - (now - last_global_call).seconds
        print(f"[Global Cooldown] {wait_time}초 대기")
        await asyncio.sleep(wait_time)
    
    url = f"{COINGECKO_API}/coins/markets"
    params = {
        "vs_currency": "usd",
        "category": category_id,
        "order": "price_change_percentage_24h_desc",
        "per_page": 50,
        "page": 1
    }
    print(f"[Coingecko Call] {datetime.now()} | category={category_id}")
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url, params=params)
        print(f"[Coingecko Response] status={r.status_code}")
        if r.status_code == 429:
            print("❌ API rate limit (429) 발생")
            return []
        r.raise_for_status()
        coins = r.json()
        
    last_called[category_id] = now
    last_global_call = now
    
    coins_sorted = sorted(
        coins,
        key=lambda c: c.get("price_change_percentage_24h", 0),
        reverse=True
    )

    seen = set()
    unique_coins = []
    for coin in coins_sorted:
        sym = coin.get("symbol", "").upper()
        if sym not in seen:
            seen.add(sym)
            unique_coins.append(coin)
        if len(unique_coins) == 3:
            break

    return unique_coins

async def send_top3_to_telegram(bot, category_id: str, coins: list):
    print(f"[Telegram Send] {datetime.now()} | category={category_id} | coins={len(coins)}")
    display_name_map = {
        "ethereum-ecosystem": "Ethereum ECO",
        "solana-ecosystem": "Solana ECO",
        "binance-smart-chain": "BNB Chain ECO",
        "meme-token": "Meme",
        "depin": "DePIN",
        "artificial-intelligence": "AI",
        "layer-1": "Layer1",
        "centralized-exchange-token-cex": "Exchanges",
        "real-world-assets-rwa": "RWA",
        "world-liberty-financial-portfolio": "world-liberty-financial-portfolio",
        "dot-ecosystem": "POLKADOT"
        
    }
    
    display_name = display_name_map.get(category_id, category_id)
    
    if not coins:
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"📊 {display_name} 카테고리에서 코인을 찾지 못했습니다."
        )
        return

    msg_lines = [f"🔥 <b>{display_name} Top 3 상승 코인 (24h)</b>\n"]
    for coin in coins:
        name = coin.get("name")
        symbol = coin.get("symbol").upper()
        price = coin.get("current_price")
        change = coin.get("price_change_percentage_24h", 0)
        msg_lines.append(f"- {name} ({symbol}) | ${price} | {change:.2f}%")

    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text="\n".join(msg_lines),
        parse_mode="HTML"
    )

@app.post("/sector")
async def sector_webhook(request: Request):
    data = await request.json()
    print(f"[Webhook Triggered] {datetime.now()} | data={data}")
    symbol = data.get("symbol", "").upper()  
    message = data.get("message") 

    if message == "UP":
        
        mapping = {
            "SOLANA.C": "solana-ecosystem",
            "BNBCHAIN.C": "binance-smart-chain",
            "ETHEREUM.C": "ethereum-ecosystem",
            "STABLE.C": "stablecoins",
            "STABLE.C.D": "stablecoins",
            "LAYER1.C": "layer-1",
            "DEPIN.C": "depin",
            "MEME.C": "meme-token",
            "EXCHANGES.C": "centralized-exchange-token-cex",
            "AI.C": "artificial-intelligence",
            "RWA.C": "real-world-assets-rwa",
            "WORLDLIBERTY.C": "world-liberty-financial-portfolio",
            "POLKADOT.C": "dot-ecosystem",
        }
        category_id = mapping.get(symbol)
        if category_id:
            coins = await get_top3_tokens(category_id)
            await send_top3_to_telegram(telegram_app.bot, category_id, coins)

    return JSONResponse(content={"ok": True})

SECTOR_NAME_MAP = {
    "SOLANA.C": "솔라나",
    "ETHEREUM.C": "이더리움",
    "WORLDLIBERTY.C": "월드 리버티 포트폴리오",
    "EXCHANGES.C": "거래소",
    "LAYER1.C": "레이어1",
    "BNBCHAIN.C": "BNB",
    "RWA.C": "RWA",
    "MEME.C": "MEME",
    "DEPIN.C": "DEPIN",
    "AI.C": "AI",
    "POLKADOT.C": "폴카닷"
}

@app.post("/sector_candle")
async def sector_candle(request: Request):
    data = await request.json()
    print(f"[Sector Candle] {datetime.now()} | data={data}")

    symbol = data.get("symbol")
    candle_interval = data.get("interval")
    candle_time = data.get("time")
    close = float(data.get("close"))
    print(f"DEBUG candle_interval={candle_interval}, type={type(candle_interval)}")

    dt_utc = datetime.fromisoformat(candle_time.replace("Z", "+00:00"))
    dt_kst = dt_utc.astimezone(KST)
    
    #
    safe_supabase_call(
        supabase.table("sector_candles").upsert(
            {
                "symbol": symbol,
                "candle_time": dt_kst.isoformat(), 
                "candle_interval": str(candle_interval),
                "close": close
            },
            on_conflict="symbol,candle_interval,candle_time"
        )
    )

    rows = safe_supabase_call(
        supabase.table("sector_candles")
        .select("id, candle_time")
        .eq("symbol", symbol)
        .eq("candle_interval", candle_interval)
        .order("candle_time", desc=True)
    )

    if rows and rows.data and len(rows.data) > 3:
        to_delete = [r["id"] for r in rows.data[3:]]
        safe_supabase_call(
            supabase.table("sector_candles").delete().in_("id", to_delete)
        )
    
    # 1D 
    if candle_interval == "1D":
        print(f"[Daily Ref] {symbol} 1D 기준가 저장 (KST {dt_kst}): {close}")
        return JSONResponse(content={"ok": True})

    # 4H CAL
    if candle_interval == "240":
        if dt_kst.hour < 9:  
            # "어제 09:00 ~ 오늘 08:59"
            start = datetime(dt_kst.year, dt_kst.month, dt_kst.day, 9, 0, tzinfo=KST) - timedelta(days=1)
        else:
            # "오늘 09:00 ~ 내일 08:59"
            start = datetime(dt_kst.year, dt_kst.month, dt_kst.day, 9, 0, tzinfo=KST)
        end = start + timedelta(days=1) - timedelta(seconds=1)
        
        ref = safe_supabase_call(
            supabase.table("sector_candles")
            .select("close")
            .eq("symbol", symbol)
            .eq("candle_interval", "1D")
            .gte("candle_time", start.isoformat())
            .lt("candle_time", end.isoformat())
            .order("candle_time", desc=True)
            .limit(1)
        )

        if ref and ref.data:
            ref_close = float(ref.data[0]["close"])
            pct = (close - ref_close) / ref_close * 100
            
            tname = SECTOR_NAME_MAP.get(symbol, symbol)
            msg = f"🔥 {tname} 섹터\n현재 변동률: {pct:.2f}%"
            await telegram_app.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=msg
            )
        else:
            print(f"[WARN] {symbol} 기준가(1D) 없음")

    return JSONResponse(content={"ok": True})


















