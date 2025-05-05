import asyncio
import httpx
import json
import logging
import re
import requests
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
from matplotlib.patches import Rectangle
from matplotlib.ticker import FuncFormatter
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters
import time
import urllib.parse

BOT_TOKEN = "TGbot的密钥"
AVE_API_KEY = "AVE的API密钥"
INTERVAL = 1
SIZE = 100
SOURCE_GROUP_ID = （监听组ID）
TARGET_GROUP_ID = （转发组ID）

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

def format_price(x, pos=None):
    s = f"{x:.10f}"
    if "." in s:
        int_part, frac_part = s.split(".")
        lead_zeros = len(frac_part) - len(frac_part.lstrip('0'))
        if lead_zeros >= 4:
            sub = str(lead_zeros).translate(str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉"))
            return f"0.0{sub}{frac_part[lead_zeros:]}"
        else:
            return str(round(x, 10)).rstrip('0').rstrip('.')
    return s

def draw_kline(token_id: str, output_path='kline_chart.png'):
    now = int(time.time())
    to_time = now - (now % 60) - 1
    from_time = to_time - (SIZE * INTERVAL * 60)
    url = f"https://prod.ave-api.com/v2/klines/token/{token_id}-solana?interval={INTERVAL}&size={SIZE}&to_time={to_time}&from_time={from_time}"
    headers = {"X-API-KEY": AVE_API_KEY}
    resp = requests.get(url, headers=headers)
    data = resp.json()
    if data["status"] != 1 or not data["data"]["points"]:
        return None
    df = pd.DataFrame([{
        "time": datetime.fromtimestamp(p["time"]),
        "open": float(p["open"]),
        "high": float(p["high"]),
        "low": float(p["low"]),
        "close": float(p["close"]),
        "volume": float(p["volume"])
    } for p in data["data"]["points"]])
    df["x"] = range(len(df))
    df.set_index("x", inplace=True)
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.yaxis.set_major_formatter(FuncFormatter(format_price))
    width = 0.4
    for idx, row in df.iterrows():
        color = 'green' if row['close'] >= row['open'] else 'red'
        lower = min(row['open'], row['close'])
        height = abs(row['close'] - row['open'])
        ax.add_patch(Rectangle((idx - width / 2, lower), width, height, color=color))
        ax.vlines(x=idx, ymin=row['low'], ymax=row['high'], color=color, linewidth=1)
    last_price = df.iloc[-1]['close']
    formatted = format_price(last_price)
    ax.text(0.5, -0.05, f'Price: {formatted}', transform=ax.transAxes, fontsize=18, color='#555555', ha='center', va='top')
    ax.set_xticks([df.index[0], df.index[-1]])
    ax.set_xticklabels([
        df['time'].iloc[0].strftime('%H:%M'),
        df['time'].iloc[-1].strftime('%H:%M')
    ], fontsize=10)
    ax.set_title("")
    ax.grid(False)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()
    return output_path

def parse_trade_message(message: str):
    result = {}
    if "bought" in message:
        result["type"] = "🟢购买"
    elif "sold" in message:
        result["type"] = "🔴出售"
    else:
        return None
    name_match = re.search(r"(🟢|🔴)\s*([^\(]+?)\s*\(", message)
    if name_match:
        result["trader"] = name_match.group(2).strip()
    token_match = re.search(r"(?:bought|sold)\s+[\d\.]+[kmbKMB]*\s+([^\s\(]+)", message)
    if token_match:
        result["token"] = token_match.group(1).strip()
    pay_match = re.search(r"(?:with|for)\s+([\d\.]+[kmbKMB]*)\s+([^\s\(]+)", message)
    if pay_match:
        result["pay_amount"] = pay_match.group(1).strip()
        result["pay_token"] = pay_match.group(2).strip()
    usd_match = re.search(r"\(\$([\d,\.]+)\)", message)
    if usd_match:
        result["usd"] = usd_match.group(1).replace(",", "")
    contract_match = re.search(r"📋\s+([a-zA-Z0-9]{20,})", message)
    if contract_match:
        result["contract"] = contract_match.group(1).strip()
    return result if "contract" in result else None

def formatSimpleTradeInfo(info: dict) -> str:
    emoji = "🟢" if "购买" in info["type"] else "🔴"
    name = info.get("trader", "")
    token = info.get("token", "")
    amount = info.get("pay_amount", "")
    pay_token = info.get("pay_token", "")
    usd = info.get("usd", "")
    contract = info.get("contract", "")
    action = info["type"]
    return f"{emoji} {name} {emoji} {action} [{token}](http://8.217.247.245/sou={token})\n💵 支付: {amount} {pay_token}($ {usd})\n📋 合约: {contract}"

async def get_token_details(contract: str) -> (str, InlineKeyboardMarkup):
    url = f"https://prod.ave-api.com/v2/tokens/{contract}-solana"
    headers = {"X-API-KEY": AVE_API_KEY}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                token = response.json()['data']['token']
                price = float(token.get('current_price_usd', 0))
                price_str = format_price(price)
                market_cap = float(token.get('market_cap', 0)) / 10000
                volume = float(token.get('tx_volume_u_24h', 0)) / 10000
                holders = token.get('holders', 0)
                audited = token.get('is_audited', False)
                appendix = json.loads(token.get('appendix', '{}'))
                website = appendix.get('website', '#')
                twitter = appendix.get('twitter', '#')
                telegram = appendix.get('telegram', '#')
                name = token.get('name', 'Unknown')

                twitter_link = ''
                if 'status/' in twitter:
                    tweet_id = twitter.split('status/')[-1]
                    twitter_link = f"[推特](http://8.217.247.245/{tweet_id})"
                elif '/i/communities/' in twitter:
                    community_id = twitter.split('/i/communities/')[-1]
                    twitter_link = f"[推特](http://8.217.247.245/sq={community_id})"
                elif twitter.startswith("https://twitter.com/") or twitter.startswith("https://x.com/"):
                    username = twitter.rstrip('/').split('/')[-1]
                    twitter_link = f"[推特](http://8.217.247.245/{username})"
                else:
                    twitter_link = f"[推特]({twitter})"

                ft_string = f"{contract} ${name} {twitter}".strip()
                ft_encoded = urllib.parse.quote(ft_string)
                ft_url = f"http://8.217.247.245/ft={ft_encoded}"

                details = (
                    f"\n💰 当前价格：[$ {price_str}](https://dexscreener.com/solana/{contract})"
                    f"\n📈 当前市值：{market_cap:.2f} 万"
                    f"\n📊 24h 成交额：{volume:.2f} 万"
                    f"\n👥 持有人数：{holders}"
                    f"\n🔗 [搜索{name}](http://8.217.247.245/sou={name})"
                    f"\n🌐 [官网]({website}) ｜ {twitter_link} ｜ 📢 [Telegram]({telegram})"
                )

                extra_buttons = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📤 发推", url=ft_url)]
                ])

                return details, extra_buttons
            return "❌ 获取详情失败", None
        except Exception as e:
            return f"❌ 请求出错: {e}", None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None or update.message.text is None:
        return
    if update.message.chat.id != SOURCE_GROUP_ID:
        return
    text = update.message.text
    parsed = parse_trade_message(text)
    if parsed:
        summary = formatSimpleTradeInfo(parsed)
        token_info, extra_buttons = await get_token_details(parsed['contract'])
        img_path = draw_kline(parsed['contract'])
        base_keyboard = [
            [InlineKeyboardButton("🔗 龙枪跳转", url=f"https://t.me/DraGun69_Bot?start={parsed['contract']}-rich75ce95c1d46a")],
            [InlineKeyboardButton("🆗 跳转OKX", url=f"http://8.217.247.245:90/{parsed['contract']}")]
        ]
        if extra_buttons:
            base_keyboard.extend(extra_buttons.inline_keyboard)
        reply_markup = InlineKeyboardMarkup(base_keyboard)

        if img_path:
            await context.bot.send_photo(
                chat_id=TARGET_GROUP_ID,
                photo=open(img_path, 'rb'),
                caption=summary + token_info,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        else:
            await context.bot.send_message(chat_id=TARGET_GROUP_ID, text=summary + token_info, reply_markup=reply_markup, parse_mode="Markdown")

if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ Bot started.")
    app.run_polling()
