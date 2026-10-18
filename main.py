import asyncio
import html
import logging
import random
import secrets
import sqlite3
import string
import os
import re
import zipfile
import tempfile
from aiogram import BaseMiddleware
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton, ErrorEvent, CopyTextButton, ForceReply, FSInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# =========================================================
# TIGER FLOW - CONFIG
# =========================================================
# Security: this token was previously shared in chat. Regenerate it in @BotFather
# before public deployment if it has ever been exposed outside your private setup.
# Uses the hosting environment token when available; otherwise uses the token supplied
# for this deployment so no manual token edit is required.
BOT_TOKEN = os.getenv("BOT_TOKEN", "8965597937:AAFDAp8sUiJl9SoiRLn34oqGJ29lDSVsD3I").strip()
ADMIN_ID = 8244207549
REQUIRED_CHANNEL = "@tigerflowyt"
SUPPORT_USERNAME = "@TIGERFLOW07"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "tiger_flow.sqlite3")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("tiger_flow")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())


class ServiceGateMiddleware(BaseMiddleware):
    """Stops non-admin interactions while preserving the admin control panel."""
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user and user.id != ADMIN_ID and get_setting("bot_enabled", "1") != "1":
            notice = get_setting(
                "bot_stopped_message",
                "⛔ <b>BOT IS CURRENTLY STOPPED</b>\n\nPlease try again later."
            )
            if isinstance(event, Message):
                await event.answer(notice)
            elif isinstance(event, CallbackQuery):
                # Telegram callback alerts have a short text limit.
                plain = re.sub(r"<[^>]+>", "", notice).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
                await event.answer(plain[:190] or "Bot is currently stopped.", show_alert=True)
            return
        return await handler(event, data)


dp.message.outer_middleware(ServiceGateMiddleware())
dp.callback_query.outer_middleware(ServiceGateMiddleware())

# Each chat has one active OTP task. A new number cancels the previous task.
OTP_TASKS = {}
ACTIVE_SESSIONS = {}


class Form(StatesGroup):
    waiting_key = State()
    admin_add_key = State()
    admin_key_action = State()
    admin_add_country = State()
    admin_edit_country = State()
    admin_remove_country = State()
    admin_add_service = State()
    admin_edit_service = State()
    admin_remove_service = State()
    admin_toggle_country = State()
    admin_toggle_service = State()
    admin_edit_text = State()
    admin_set_channel = State()
    admin_set_support = State()
    admin_set_logo = State()
    admin_set_service_emoji = State()
    admin_icon_search = State()
    keybuy_txid = State()
    admin_keybuy_key = State()
    admin_broadcast = State()
    admin_broadcast_button = State()
    admin_broadcast_button_text = State()
    admin_keybuy_address = State()
    admin_keybuy_price = State()
    admin_keybuy_banner = State()
    admin_set_icon = State()
    admin_text_find = State()
    admin_text_replace = State()
    admin_emoji_target = State()
    admin_emoji_replace = State()
    admin_stopped_message = State()


def conn():
    c = sqlite3.connect(DB_FILE)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    c = conn()
    c.execute("""CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY, username TEXT DEFAULT '', joined INTEGER DEFAULT 0,
        verified INTEGER DEFAULT 0, verified_key TEXT DEFAULT '', created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS user_numbers(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, username TEXT DEFAULT '',
        service TEXT DEFAULT '', country TEXT DEFAULT '', number TEXT NOT NULL, created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS access_keys(
        key TEXT PRIMARY KEY, owner_id INTEGER, active INTEGER DEFAULT 1, created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS countries(
        code TEXT PRIMARY KEY, name TEXT NOT NULL, prefix TEXT NOT NULL,
        flag TEXT DEFAULT '🌍', active INTEGER DEFAULT 1, custom_emoji_id TEXT DEFAULT '')""")
    c.execute("""CREATE TABLE IF NOT EXISTS services(
        code TEXT PRIMARY KEY, name TEXT NOT NULL, emoji TEXT DEFAULT '📱', active INTEGER DEFAULT 1,
        custom_emoji_id TEXT DEFAULT '')""")
    c.execute("""CREATE TABLE IF NOT EXISTS settings(name TEXT PRIMARY KEY, value TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS key_buy_requests(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL, username TEXT DEFAULT '',
        txid TEXT NOT NULL, status TEXT DEFAULT 'pending',
        admin_key TEXT DEFAULT '', created_at TEXT, reviewed_at TEXT
    )""")

    # Backward-compatible migration for the old countries table.
    cols = {r[1] for r in c.execute("PRAGMA table_info(countries)").fetchall()}
    if "flag" not in cols:
        c.execute("ALTER TABLE countries ADD COLUMN flag TEXT DEFAULT '🌍'")
    country_cols = {r[1] for r in c.execute("PRAGMA table_info(countries)").fetchall()}
    if "custom_emoji_id" not in country_cols:
        c.execute("ALTER TABLE countries ADD COLUMN custom_emoji_id TEXT DEFAULT ''")

    service_cols = {r[1] for r in c.execute("PRAGMA table_info(services)").fetchall()}
    if "custom_emoji_id" not in service_cols:
        c.execute("ALTER TABLE services ADD COLUMN custom_emoji_id TEXT DEFAULT ''")

    countries = [
        ("IN", "India", "+91", "🇮🇳", "5291809418487290691"),
        ("PK", "Pakistan", "+92", "🇵🇰", "5291825606219029010"),
        ("BD", "Bangladesh", "+880", "🇧🇩", "5291824687096027834"),
    ]
    c.execute("DELETE FROM countries WHERE code NOT IN ('IN','PK','BD')")
    for code, name, prefix, flag, eid in countries:
        c.execute("INSERT OR IGNORE INTO countries(code,name,prefix,flag,active,custom_emoji_id) VALUES(?,?,?,?,1,?)",
                  (code, name, prefix, flag, eid))
        c.execute("UPDATE countries SET name=?,prefix=?,flag=?,active=1,custom_emoji_id=CASE WHEN custom_emoji_id IS NULL OR custom_emoji_id='' THEN ? ELSE custom_emoji_id END WHERE code=?",
                  (name, prefix, flag, eid, code))

    services = [
        ("whatsapp", "WhatsApp", "", "5264943818230214577"),
        ("telegram", "Telegram", "", "5453875134613958282"),
        ("instagram", "Instagram", "", "5454400924510334242"),
        ("facebook", "Other Service", "", "5454340696183943190"),
    ]
    for code, name, emoji, eid in services:
        c.execute("INSERT OR IGNORE INTO services(code,name,emoji,active,custom_emoji_id) VALUES(?,?,?,?,?)",
                  (code, name, emoji, 1, eid))
        c.execute("UPDATE services SET custom_emoji_id=CASE WHEN custom_emoji_id IS NULL OR custom_emoji_id='' THEN ? ELSE custom_emoji_id END WHERE code=?", (eid, code))
    # Final UI naming/order update: Telegram stays Telegram; Facebook is shown as Other Service.
    c.execute("UPDATE services SET name=? WHERE code=?", ("Telegram", "telegram"))
    c.execute("UPDATE services SET name=? WHERE code=?", ("Other Service", "facebook"))
    c.execute("UPDATE services SET emoji=? WHERE code IN ('whatsapp','telegram','instagram','facebook')", ("",))
    # Instagram is intentionally hidden from the user-facing service selector.
    c.execute("UPDATE services SET active=0 WHERE code=?", ("instagram",))

    settings = {
        "channel": REQUIRED_CHANNEL,
        "support": SUPPORT_USERNAME,
        "title": "👋 <b>WELCOME! {name}</b>\n\nThank you for joining <b>DRAGON SMS</b> — your hub for convenient demo OTP experiences.\n\nExplore the available services and countries, and enjoy a smooth, simple experience.\n\n<i>For testing and demonstration purposes.</i>",
        "logo_emoji": "🐉",
        "get_number": "📞 GET NUMBER",
        "countries": "🌍 TRAFFIC COUNTRY",
        "admin_button": "👑 ADMIN PANEL",
        "select_service": "SELECT SERVICE",
        "select_country": "Select Your Country",
        "icon_select_service": "6086761079898117431",
        "icon_select_country": "6339280615459789282",
        "icon_back": "6052883185321121442",
        "icon_access_key": "6233232736140663096",
        "icon_warning": "6052867933892254014",
        "icon_wrong_key": "6183936527446316985",
        "icon_number_header": "6053128350644311646",
        "icon_service_label": "6260478625687018336",
        "icon_country_label": "6311881228077245225",
        "icon_number_label": "6204108584381322968",
        "icon_otp_title": "6053193097276298985",
        "number_header": "📲 These numbers are activated and ready to receive SMS.",
        "waiting_text": "⏳ WAITING FOR OTP...........................",
        "otp_title": "✉️ New DID Received!",
        "logo_emoji_id": "",
        "key_buy_address": "0xAae6a259823DF8CF2D948272344c8F2ED556d4dE",
        "key_buy_amount": "1.00",
        "key_buy_enabled": "1",
        "traffic_title": "📊 Live Traffic Update",
        "traffic_BD": "High",
        "traffic_IN": "High",
        "traffic_PK": "High",
        "bot_enabled": "1",
        "bot_stopped_message": "⛔ <b>BOT IS CURRENTLY STOPPED</b>\n\nPlease try again later. We will be back soon!",
    }
    for name, value in settings.items():
        c.execute("INSERT OR IGNORE INTO settings(name,value) VALUES(?,?)", (name, value))
    # If an older Tiger Flow database is reused, migrate only the old default branding.
    c.execute("UPDATE settings SET value=? WHERE name='title' AND value IN ('👋 <b>Welcome to TIGER FLOW</b>', '<b>Welcome to TIGER FLOW</b>', '<b>WELCOME TO DRAGON SMS</b>', '👋 <b>WELCOME TO DRAGON SMS</b>', '👋 <b>WELCOME TO {name}</b>')",
              ("👋 <b>WELCOME! {name}</b>\n\nThank you for joining <b>DRAGON SMS</b> — your hub for convenient demo OTP experiences.\n\nExplore the available services and countries, and enjoy a smooth, simple experience.\n\n<i>For testing and demonstration purposes.</i>",))
    c.execute("UPDATE settings SET value='🌍 TRAFFIC COUNTRY' WHERE name='countries' AND value IN ('🌍 AVAILABLE COUNTRY','AVAILABLE COUNTRY','🌍 TRAFFIC COUNTRY')")
    c.execute("UPDATE settings SET value='🐉' WHERE name='logo_emoji' AND (value IS NULL OR value='')")
    # Upgrade the previous default support username, without overwriting a custom admin setting.
    c.execute("UPDATE settings SET value=? WHERE name='support' AND value IN ('@tigerflow99','@TIGERFLOW99')", (SUPPORT_USERNAME,))

    # One-time repair: older approvals incorrectly marked buyers verified before they entered the key.
    fix = c.execute("SELECT value FROM settings WHERE name='keybuy_verify_fix_v1'").fetchone()
    if not fix:
        c.execute("UPDATE users SET verified=0, verified_key=NULL WHERE user_id IN (SELECT DISTINCT user_id FROM key_buy_requests WHERE status='approved')")
        c.execute("INSERT OR REPLACE INTO settings(name,value) VALUES('keybuy_verify_fix_v1','done')")

    c.execute("INSERT OR IGNORE INTO access_keys(key,owner_id,active,created_at) VALUES(?,?,?,?)",
              ("TIGER-START-2026", None, 1, datetime.now().isoformat()))
    c.commit()
    c.close()


def get_setting(name, fallback=""):
    c = conn()
    row = c.execute("SELECT value FROM settings WHERE name=?", (name,)).fetchone()
    c.close()
    return row["value"] if row else fallback


def set_setting(name, value):
    c = conn()
    c.execute("INSERT OR REPLACE INTO settings(name,value) VALUES(?,?)", (name, value))
    c.commit()
    c.close()


def is_admin(uid):
    return uid == ADMIN_ID


def save_user(m: Message):
    c = conn()
    c.execute("""INSERT INTO users(user_id,username,created_at) VALUES(?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET username=excluded.username""",
              (m.from_user.id, m.from_user.username or "", datetime.now().isoformat()))
    c.commit()
    c.close()


def custom_emoji(eid: str, fallback: str) -> str:
    eid = (eid or "").strip()
    if eid:
        return f'<tg-emoji emoji-id="{html.escape(eid, quote=True)}">{html.escape(fallback)}</tg-emoji>'
    return fallback


def icon_button(eid: str, text: str, callback_data: str | None = None, url: str | None = None):
    kwargs = {"text": text, "icon_custom_emoji_id": (eid or None)}
    if callback_data is not None:
        kwargs["callback_data"] = callback_data
    if url is not None:
        kwargs["url"] = url
    return InlineKeyboardButton(**kwargs)


def label_text(setting_name: str, icon_setting: str, fallback_icon: str) -> str:
    label = get_setting(setting_name, "").strip()
    # Keep only the label text when an older build stored the fallback emoji in it.
    if label.startswith(fallback_icon):
        label = label[len(fallback_icon):].strip()
    fallback_icon = get_setting("emoji_" + setting_name, fallback_icon) or fallback_icon
    return f"{custom_emoji(get_setting(icon_setting, ''), fallback_icon)} {html.escape(label)}"


def logo_html():
    # Admin-controlled ordinary Unicode emoji shown in bot messages.
    return html.escape(get_setting("logo_emoji", "🐉") or "🐉")


def welcome_text(user=None):
    title = get_setting("title", "👋 <b>WELCOME! {name}</b>")
    name = html.escape((getattr(user, "first_name", None) or "there").strip()) if user else "there"
    # Keep one greeting emoji and substitute only the name placeholder. Do not
    # prepend the configurable logo here, which previously caused duplicate 👋.
    return title.replace("{name}", name)


def user_has_completed_key_buy(uid: int) -> bool:
    # A purchase only unlocks the menu while its linked key remains active.
    c = conn()
    row = c.execute("SELECT 1 FROM access_keys WHERE owner_id=? AND active=1 LIMIT 1", (uid,)).fetchone()
    c.close()
    return bool(row)


def user_keyboard(uid=None):
    rows = [
        [KeyboardButton(text=get_setting("get_number", "📞 GET NUMBER")),
         KeyboardButton(text=get_setting("countries", "🌍 AVAILABLE COUNTRY"))],
    ]
    if uid is not None and get_setting("key_buy_enabled", "1") == "1" and not user_has_completed_key_buy(uid):
        rows.append([KeyboardButton(text="🔑 KEY BUY")])
    if uid == ADMIN_ID:
        rows.append([KeyboardButton(text=get_setting("admin_button", "👑 ADMIN PANEL"))])
    return ReplyKeyboardMarkup(
        keyboard=rows, resize_keyboard=True, is_persistent=False, one_time_keyboard=False, input_field_placeholder="Choose an option…"
    )


def channel_keyboard():
    channel = get_setting("channel", REQUIRED_CHANNEL).strip()
    join_url = channel if channel.startswith("https://t.me/") else f"https://t.me/{channel.lstrip('@')}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 JOIN CHANNEL", url=join_url)],
        [InlineKeyboardButton(text="✅ VERIFY", callback_data="verify_channel")],
    ])


def key_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 ENTER KEY", callback_data="enter_key")]
    ])


def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 KEY MANAGEMENT", callback_data="adm:keys")],
        [InlineKeyboardButton(text="🌍 COUNTRY MANAGEMENT", callback_data="adm:countries")],
        [InlineKeyboardButton(text="📱 SERVICE MANAGEMENT", callback_data="adm:services")],
        [InlineKeyboardButton(text="🎨 UI / EMOJI & LOGO", callback_data="adm:ui")],
        [InlineKeyboardButton(text="📊 STATISTICS", callback_data="adm:stats")],
        [InlineKeyboardButton(text="🛒 KEY BUY", callback_data="adm:keybuy")],
        [InlineKeyboardButton(text="📣 BROADCAST", callback_data="adm:broadcast")],
        [InlineKeyboardButton(text="👥 USERS", callback_data="adm:users")],
        [InlineKeyboardButton(text="🟢 START / ⛔ STOP BOT", callback_data="adm:control")],
        [InlineKeyboardButton(text="💾 BACKUP", callback_data="adm:backup")],
    ])


def back_admin():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ ADMIN PANEL", callback_data="adm:home")]
    ])


async def is_joined(uid: int) -> bool:
    channel = get_setting("channel", REQUIRED_CHANNEL).strip()
    try:
        member = await bot.get_chat_member(channel, uid)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        log.warning("Membership check failed for %s: %r", channel, e)
        return False


async def show_gate(m: Message):
    await m.answer(f"{logo_html()} <b>JOIN CHANNEL FIRST</b>\n\nJoin the required channel, then press VERIFY.",
                   reply_markup=channel_keyboard())


@dp.message(F.text.regexp(r"^/start(?:@[A-Za-z0-9_]+)?(?:\s.*)?$"))
async def start(m: Message, state: FSMContext):
    # Robust /start handler: works from any FSM state and also accepts /start@bot
    # and deep-link payloads. It is registered before all state-specific handlers.
    await state.clear()
    save_user(m)
    c = conn()
    c.execute("UPDATE users SET joined=1 WHERE user_id=?", (m.from_user.id,))
    c.commit(); c.close()
    await m.answer(welcome_text(m.from_user), reply_markup=user_keyboard(m.from_user.id))


@dp.callback_query(F.data == "verify_channel")
async def verify_channel(q: CallbackQuery):
    if not await is_joined(q.from_user.id):
        await q.answer("❌ Channel verification failed. Join the channel first.", show_alert=True)
        return
    c = conn()
    c.execute("UPDATE users SET joined=1 WHERE user_id=?", (q.from_user.id,))
    c.commit(); c.close()
    await q.answer("✅ Channel verified.")
    await q.message.answer(welcome_text(q.from_user), reply_markup=user_keyboard(q.from_user.id))


@dp.callback_query(F.data == "enter_key")
async def enter_key(q: CallbackQuery, state: FSMContext):
    await q.answer()
    await state.set_state(Form.waiting_key)
    await q.message.answer("🔑 Enter your key below:\n\n⚠️ Without the correct key, numbers will not be shown.", reply_markup=ForceReply(input_field_placeholder="Enter key here...", selective=True))


@dp.message(Form.waiting_key)
async def receive_key(m: Message, state: FSMContext):
    key = (m.text or "").strip()
    c = conn()
    keyrow = c.execute("SELECT * FROM access_keys WHERE key=? AND active=1", (key,)).fetchone()
    if not keyrow:
        c.close()
        await state.clear()
        await m.answer(f"{custom_emoji(get_setting('icon_wrong_key', ""), "❌")} <b>Wrong key!</b>\n\nPlease enter the correct key.\n\n{custom_emoji(get_setting('icon_warning', ""), "⚠️")} Without the correct key, numbers will not be shown.", reply_markup=user_keyboard(m.from_user.id))
        return
    if keyrow["owner_id"] is not None and keyrow["owner_id"] != m.from_user.id:
        c.close()
        await state.clear()
        await m.answer(f"{custom_emoji(get_setting('icon_wrong_key', ""), "❌")} <b>Wrong key!</b>\n\nPlease enter the correct key.\n\n{custom_emoji(get_setting('icon_warning', ""), "⚠️")} Without the correct key, numbers will not be shown.", reply_markup=user_keyboard(m.from_user.id))
        return
    current = c.execute("SELECT verified,verified_key FROM users WHERE user_id=?", (m.from_user.id,)).fetchone()
    if current and current["verified"] and current["verified_key"] != key:
        c.close(); await state.clear()
        await m.answer("⚠️ Your Telegram ID is already verified with another key.")
        return
    c.execute("UPDATE access_keys SET owner_id=? WHERE key=?", (m.from_user.id, key))
    c.execute("UPDATE users SET verified=1,verified_key=?,joined=1 WHERE user_id=?", (key, m.from_user.id))
    c.commit(); c.close()
    data = await state.get_data()
    pending_service = data.get("pending_service_code")
    pending_country = data.get("pending_country_code")
    await state.clear()
    await m.answer("✅ <b>ID VERIFIED</b>\n\nYou can now use all available features.", reply_markup=user_keyboard(m.from_user.id))
    if pending_service and pending_country:
        await deliver_number(m.chat.id, pending_service, pending_country)


async def authorized_user(m: Message) -> bool:
    if not await is_user_verified(m.from_user.id):
        await m.answer("🔐 Please enter a valid key to verify your ID.", reply_markup=key_keyboard())
        return False
    return True


def service_keyboard():
    c = conn()
    # Fixed user-facing order: WhatsApp, Telegram, Instagram, Other Service.
    rows = c.execute("""SELECT code,name,emoji,custom_emoji_id FROM services
                       WHERE active=1 AND code != 'instagram'
                       ORDER BY CASE code
                           WHEN 'whatsapp' THEN 1
                           WHEN 'telegram' THEN 2
                           WHEN 'instagram' THEN 3
                           WHEN 'facebook' THEN 4
                           ELSE 5 END, name""").fetchall()
    c.close()
    service_buttons = [icon_button(r['custom_emoji_id'], (f"{r['emoji']} " if r['emoji'] else "") + r['name'], callback_data=f"svc:{r['code']}") for r in rows]
    buttons = [service_buttons[i:i+2] for i in range(0, len(service_buttons), 2)]
    buttons.append([icon_button(get_setting("icon_back", ""), "BACK", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def handle_admin_text_by_state(m: Message, state: FSMContext, current_state: str) -> bool:
    """Reliable fallback for admin text input.

    Some hosting/runtime combinations can fail to dispatch a state-specific
    message handler. This single dispatcher keeps every admin text action
    functional even in that case.
    """
    if not is_admin(m.from_user.id):
        return False
    text = (m.text or "").strip()

    if current_state == Form.admin_keybuy_address.state:
        value = text
        if not re.fullmatch(r"0x[a-fA-F0-9]{40}", value):
            await m.answer("❌ Invalid BEP-20 address. It must be 0x + 40 hexadecimal characters.")
            return True
        set_setting("key_buy_address", value)
        await state.clear()
        await m.answer("✅ BEP-20 address updated.", reply_markup=back_admin())
        return True

    if current_state == Form.admin_keybuy_price.state:
        value = text
        if not re.fullmatch(r"(?:\d+)(?:\.\d{1,2})?", value) or float(value) <= 0:
            await m.answer("❌ Invalid price. Example: 1.00")
            return True
        set_setting("key_buy_amount", f"{float(value):.2f}")
        await state.clear()
        await m.answer(f"✅ Key price updated to <b>${float(value):.2f} USDT</b>.", reply_markup=back_admin())
        return True

    if current_state == Form.admin_keybuy_key.state:
        key = text
        if len(key) < 3 or "|" in key:
            await m.answer("❌ Invalid key. Send the exact key to deliver.")
            return True
        data = await state.get_data()
        rid = data.get("keybuy_request_id")
        c = conn()
        r = c.execute("SELECT * FROM key_buy_requests WHERE id=? AND status='pending'", (rid,)).fetchone()
        if not r:
            c.close()
            await state.clear()
            await m.answer("❌ Request is no longer pending.", reply_markup=back_admin())
            return True
        existing = c.execute("SELECT key FROM access_keys WHERE key=?", (key,)).fetchone()
        if existing:
            c.close()
            await m.answer("⚠️ This key already exists in Key Management. Send a different key.")
            return True
        c.execute("INSERT INTO access_keys(key,owner_id,active,created_at) VALUES(?,?,1,?)", (key, r['user_id'], datetime.now().isoformat()))
        c.execute("UPDATE users SET verified=0,verified_key=NULL,joined=1 WHERE user_id=?", (r['user_id'],))
        c.execute("UPDATE key_buy_requests SET status='approved',admin_key=?,reviewed_at=? WHERE id=?", (key, datetime.now().isoformat(), rid))
        c.commit()
        c.close()
        await state.clear()
        try:
            await bot.send_message(r['user_id'], f"✅ <b>PAYMENT SUCCESSFUL</b>\n\n🔑 Your Key:\n<code>{html.escape(key)}</code>\n\nPayment approved. Send this key to the bot to verify your Telegram ID and unlock number access.", reply_markup=user_keyboard(r['user_id']))
        except Exception as e:
            log.warning("Could not notify buyer %s: %r", r['user_id'], e)
        await m.answer(f"✅ <b>REQUEST #{rid} APPROVED</b>\n\nKey delivered to the user.", reply_markup=back_admin())
        return True

    if current_state == Form.admin_broadcast.state:
        if text.lower() == "/cancel":
            await state.clear()
            await m.answer("❌ Broadcast cancelled.", reply_markup=back_admin())
            return True
        await state.update_data(broadcast_chat_id=m.chat.id, broadcast_message_id=m.message_id)
        await state.set_state(Form.admin_broadcast_button)
        await m.answer(
            "🔗 <b>ADD A BUTTON?</b>\n\n"
            "Send the button link or username first (examples: <code>https://example.com</code>, <code>https://t.me/TIGER_FLOW_OTP_BOT</code>, or <code>@TIGER_FLOW_OTP_BOT</code>).\n\n"
            "Then I will ask what text should appear on the button.\n"
            "Send <code>/skip</code> to broadcast without a button, or <code>/cancel</code> to cancel."
        )
        return True

    if current_state == Form.admin_broadcast_button.state:
        if text.lower() == "/cancel":
            await state.clear()
            await m.answer("❌ Broadcast cancelled.", reply_markup=back_admin())
            return True
        if text.lower() == "/skip":
            await state.update_data(broadcast_button_url=None, broadcast_button_label=None)
            data = await state.get_data()
            source_chat, source_message = data.get("broadcast_chat_id"), data.get("broadcast_message_id")
            c = conn(); users = c.execute("SELECT user_id FROM users").fetchall(); c.close()
            sent = failed = 0
            for row in users:
                try:
                    await bot.copy_message(chat_id=row["user_id"], from_chat_id=source_chat, message_id=source_message)
                    sent += 1
                except Exception as e:
                    failed += 1; log.warning("Broadcast failed for %s: %r", row["user_id"], e)
                await asyncio.sleep(0.04)
            await state.clear()
            await m.answer(f"📣 <b>BROADCAST COMPLETE</b>\n\n✅ Sent: {sent}\n❌ Failed: {failed}", reply_markup=back_admin())
            return True
        url = text.strip()
        if url.startswith("@"): url = "https://t.me/" + url[1:]
        elif url.startswith("t.me/"): url = "https://" + url
        if not url.startswith(("https://", "http://")):
            await m.answer("❌ Send a full link or @username. Example: <code>@TIGER_FLOW_OTP_BOT</code>, or /skip.")
            return True
        await state.update_data(broadcast_button_url=url)
        await state.set_state(Form.admin_broadcast_button_text)
        await m.answer("✍️ Now send the text to display on the button (example: <code>OPEN BOT</code>).\nSend /cancel to cancel.")
        return True

    if current_state == Form.admin_broadcast_button_text.state:
        if text.lower() == "/cancel":
            await state.clear(); await m.answer("❌ Broadcast cancelled.", reply_markup=back_admin()); return True
        label = text.strip()
        if not label or len(label) > 64 or label.lower() in ("/skip",):
            await m.answer("❌ Send a button label (1–64 characters), or /cancel."); return True
        data = await state.get_data()
        url = data.get("broadcast_button_url")
        markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, url=url)]])
        source_chat, source_message = data.get("broadcast_chat_id"), data.get("broadcast_message_id")
        c = conn(); users = c.execute("SELECT user_id FROM users").fetchall(); c.close()
        sent = failed = 0
        for row in users:
            try:
                await bot.copy_message(chat_id=row["user_id"], from_chat_id=source_chat, message_id=source_message, reply_markup=markup)
                sent += 1
            except Exception as e:
                failed += 1; log.warning("Broadcast failed for %s: %r", row["user_id"], e)
            await asyncio.sleep(0.04)
        await state.clear()
        await m.answer(f"📣 <b>BROADCAST COMPLETE</b>\n\n✅ Sent: {sent}\n❌ Failed: {failed}", reply_markup=back_admin())
        return True

    if current_state == Form.admin_add_key.state:
        raw = text
        key = "TIGER-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(10)) if raw.upper() == "RANDOM" else raw
        if len(key) < 3 or "|" in key:
            await m.answer("❌ Invalid key. Send a key with at least 3 characters, or send RANDOM.")
            return True
        c = conn()
        try:
            c.execute("INSERT INTO access_keys(key,owner_id,active,created_at) VALUES(?,?,1,?)", (key, None, datetime.now().isoformat()))
            c.commit()
        except sqlite3.IntegrityError:
            c.close()
            await m.answer("⚠️ This key already exists. Send another key or RANDOM.")
            return True
        c.close(); await state.clear()
        await m.answer(f"✅ <b>KEY CREATED</b>\n\n<code>{html.escape(key)}</code>", reply_markup=back_admin())
        return True

    if current_state == Form.admin_key_action.state:
        data = await state.get_data(); action = data.get("key_action")
        key = text
        c = conn(); row = c.execute("SELECT active FROM access_keys WHERE key=?", (key,)).fetchone()
        if not row:
            c.close(); await state.clear(); await m.answer("❌ Key not found.", reply_markup=back_admin()); return True
        if action == "toggle":
            new = 0 if row["active"] else 1
            c.execute("UPDATE access_keys SET active=? WHERE key=?", (new, key))
            msg = "enabled" if new else "disabled"
        else:
            c.execute("UPDATE access_keys SET owner_id=NULL WHERE key=?", (key,))
            msg = "owner reset"
        c.commit(); c.close(); await state.clear(); await m.answer(f"✅ Key {msg}.", reply_markup=back_admin()); return True

    if current_state == Form.admin_add_country.state:
        parts=[x.strip() for x in text.split("|")]
        if len(parts)!=4:
            await m.answer("❌ Format: CODE|Country Name|Prefix|Flag"); return True
        code,name,prefix,flag=parts
        c=conn(); c.execute("INSERT OR REPLACE INTO countries(code,name,prefix,flag,active,custom_emoji_id) VALUES(?,?,?,?,1,COALESCE((SELECT custom_emoji_id FROM countries WHERE code=?),''))", (code.upper(),name,prefix,flag,code.upper())); c.commit(); c.close(); await state.clear()
        await m.answer(f"✅ Country added: {flag} {name} ({prefix})", reply_markup=back_admin()); return True

    if current_state == Form.admin_edit_country.state:
        parts=[x.strip() for x in text.split("|")]
        if len(parts)!=4:
            await m.answer("❌ Format: CODE|New Name|New Prefix|Flag"); return True
        code,name,prefix,flag=parts
        c=conn(); c.execute("UPDATE countries SET name=?,prefix=?,flag=? WHERE code=?", (name,prefix,flag,code.upper())); changed=c.total_changes; c.commit(); c.close(); await state.clear()
        await m.answer("✅ Country updated." if changed else "❌ Country not found.", reply_markup=back_admin()); return True

    if current_state == Form.admin_remove_country.state:
        code=text.upper(); c=conn(); c.execute("DELETE FROM countries WHERE code=?", (code,)); changed=c.total_changes; c.commit(); c.close(); await state.clear()
        await m.answer("✅ Country removed." if changed else "❌ Country not found.", reply_markup=back_admin()); return True

    if current_state == Form.admin_toggle_country.state:
        code=text.upper(); c=conn(); row=c.execute("SELECT active FROM countries WHERE code=?", (code,)).fetchone()
        if not row:
            c.close(); await state.clear(); await m.answer("❌ Country not found.", reply_markup=back_admin()); return True
        new=0 if row["active"] else 1; c.execute("UPDATE countries SET active=? WHERE code=?", (new,code)); c.commit(); c.close(); await state.clear()
        await m.answer("✅ Country enabled." if new else "⛔ Country disabled.", reply_markup=back_admin()); return True

    if current_state == Form.admin_add_service.state:
        parts=[x.strip() for x in text.split("|")]
        if len(parts)!=3:
            await m.answer("❌ Format: code|Name|Emoji"); return True
        code,name,emoji=parts
        c=conn(); c.execute("INSERT OR REPLACE INTO services(code,name,emoji,active) VALUES(?,?,?,1)", (code.lower(),name,emoji)); c.commit(); c.close(); await state.clear()
        await m.answer(f"✅ Service added: {emoji} {name}", reply_markup=back_admin()); return True

    if current_state == Form.admin_edit_service.state:
        parts=[x.strip() for x in text.split("|")]
        if len(parts)!=3:
            await m.answer("❌ Format: code|New Name|New Emoji"); return True
        code,name,emoji=parts
        c=conn(); c.execute("UPDATE services SET name=?,emoji=? WHERE code=?", (name,emoji,code.lower())); changed=c.total_changes; c.commit(); c.close(); await state.clear()
        await m.answer("✅ Service updated." if changed else "❌ Service not found.", reply_markup=back_admin()); return True

    if current_state == Form.admin_remove_service.state:
        code=text.lower(); c=conn(); c.execute("DELETE FROM services WHERE code=?", (code,)); changed=c.total_changes; c.commit(); c.close(); await state.clear()
        await m.answer("✅ Service removed." if changed else "❌ Service not found.", reply_markup=back_admin()); return True

    if current_state == Form.admin_toggle_service.state:
        code=text.lower(); c=conn(); row=c.execute("SELECT active FROM services WHERE code=?", (code,)).fetchone()
        if not row:
            c.close(); await state.clear(); await m.answer("❌ Service not found.", reply_markup=back_admin()); return True
        new=0 if row["active"] else 1; c.execute("UPDATE services SET active=? WHERE code=?", (new,code)); c.commit(); c.close(); await state.clear()
        await m.answer("✅ Service enabled." if new else "⛔ Service disabled.", reply_markup=back_admin()); return True

    if current_state == Form.admin_edit_text.state:
        parts=text.split("|",1)
        allowed={"title","get_number","countries","admin_button","select_service","number_header","waiting_text","otp_title"}
        if len(parts)!=2 or parts[0] not in allowed:
            await m.answer("❌ Invalid setting format. Example: get_number|📞 GET NUMBER"); return True
        set_setting(parts[0],parts[1]); await state.clear(); await m.answer("✅ Updated.", reply_markup=back_admin()); return True

    if current_state == Form.admin_set_logo.state:
        value=text.strip()
        if not value:
            await m.answer("❌ Please send an emoji, or send <code>off</code>."); return True
        set_setting("logo_emoji", "🐉" if value.lower()=="off" else value)
        await state.clear(); await m.answer("✅ Message logo emoji updated: " + html.escape(get_setting("logo_emoji", "🐉")), reply_markup=back_admin()); return True

    if current_state == Form.admin_set_service_emoji.state:
        parts=text.split("|",1)
        if len(parts)!=2:
            await m.answer("❌ Format: service_code|custom_emoji_id"); return True
        code,eid=parts[0].strip().lower(),parts[1].strip(); c=conn(); row=c.execute("SELECT code FROM services WHERE code=?", (code,)).fetchone()
        if not row:
            c.close(); await state.clear(); await m.answer("❌ Service not found.", reply_markup=back_admin()); return True
        if eid.lower()=="off": eid=""
        c.execute("UPDATE services SET custom_emoji_id=? WHERE code=?", (eid,code)); c.commit(); c.close(); await state.clear(); await m.answer("✅ Service custom emoji ID updated.", reply_markup=back_admin()); return True

    if current_state == Form.admin_set_channel.state:
        value=text
        if value.startswith("https://t.me/"): value="@"+value.rstrip("/").split("/")[-1]
        if not value.startswith("@"):
            await m.answer("❌ Use @channelusername or a public t.me link."); return True
        set_setting("channel",value); await state.clear(); await m.answer(f"✅ Required channel changed to {html.escape(value)}", reply_markup=back_admin()); return True

    if current_state == Form.admin_icon_search.state:
        query=text.lower(); matches=[]
        for key,(label,target) in icon_catalog().items():
            if query in key.lower() or query in label.lower(): matches.append((key,label,target))
        if not matches:
            await m.answer("❌ No icon found. Try whatsapp, country, number, key, warning, wrong, back."); return True
        buttons=[]
        for key,label,target in matches:
            eid=icon_current_id(target)
            buttons.append([InlineKeyboardButton(text=f"{label} — {'SET' if eid else 'NOT SET'}", callback_data=f"adm:iconpick:{key}", icon_custom_emoji_id=eid or None)])
        await state.clear(); await m.answer("🔎 <b>SELECT AN ICON TO CHANGE</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)); return True

    if current_state == Form.admin_set_icon.state:
        value=text; data=await state.get_data(); target=data.get("icon_target")
        if not target: await state.clear(); await m.answer("❌ Icon target expired.", reply_markup=back_admin()); return True
        eid="" if value.lower()=="off" else value
        if eid and not eid.isdigit(): await m.answer("❌ Custom emoji ID must be numeric, or send <code>off</code>."); return True
        if target.startswith("service:"):
            code=target.split(":",1)[1]; c=conn(); c.execute("UPDATE services SET custom_emoji_id=? WHERE code=?",(eid,code)); changed=c.total_changes; c.commit(); c.close()
        elif target.startswith("country:"):
            code=target.split(":",1)[1]; c=conn(); c.execute("UPDATE countries SET custom_emoji_id=? WHERE code=?",(eid,code)); changed=c.total_changes; c.commit(); c.close()
        else: set_setting(target,eid); changed=1
        await state.clear(); await m.answer("✅ <b>Icon updated successfully.</b>" if changed else "❌ Target not found.", reply_markup=back_admin()); return True

    if current_state == Form.admin_set_support.state:
        value=text
        if not value.startswith("@"):
            await m.answer("❌ Use @username."); return True
        set_setting("support",value); await state.clear(); await m.answer("✅ Support updated.", reply_markup=back_admin()); return True

    if current_state == Form.admin_text_replace.state:
        data = await state.get_data(); key = data.get("ui_text_key")
        if key not in TEXT_TARGETS:
            await state.clear(); await m.answer("❌ Text selection expired."); return True
        if key.startswith("traffic_") and key != "traffic_title":
            value=text.strip().title()
            if value not in ("High", "Medium", "Low"):
                await m.answer("❌ শুধু High, Medium অথবা Low পাঠাও।"); return True
            text=value
        set_setting(key, text)
        await state.clear(); await m.answer("✅ লেখা পরিবর্তন করা হয়েছে।", reply_markup=back_admin()); return True

    if current_state == Form.admin_emoji_replace.state:
        data = await state.get_data(); target = data.get("ui_emoji_target")
        if not text:
            await m.answer("❌ একটি ইমোজি পাঠাও।"); return True
        if target == "logo_emoji":
            set_setting("logo_emoji", text)
        elif target.startswith("service:"):
            code=target.split(":",1)[1]; c=conn(); c.execute("UPDATE services SET emoji=?,custom_emoji_id='' WHERE code=?",(text,code)); c.commit(); c.close()
        elif target.startswith("country:"):
            code=target.split(":",1)[1]; c=conn(); c.execute("UPDATE countries SET flag=?,custom_emoji_id='' WHERE code=?",(text,code)); c.commit(); c.close()
        else:
            set_setting("emoji_"+target, text)
        await state.clear(); await m.answer("✅ ইমোজি পরিবর্তন করা হয়েছে।", reply_markup=back_admin()); return True

    return False


@dp.message()
async def general_message_router(m: Message, state: FSMContext):
    # Hard fallback for /start. This also works if a stale FSM state or
    # command-routing quirk prevents CommandStart() from reaching the
    # dedicated handler. It always resets the conversation to the start flow.
    text = (m.text or "").strip()
    if text.startswith("/start") and (text == "/start" or text.startswith("/start@")):
        await state.clear()
        save_user(m)
        c = conn()
        c.execute("UPDATE users SET joined=1 WHERE user_id=?", (m.from_user.id,))
        c.commit(); c.close()
        await m.answer(welcome_text(m.from_user), reply_markup=user_keyboard(m.from_user.id))
        return

    current_state = await state.get_state()

    # Admin can replace the KEY BUY payment banner by sending a photo.
    if current_state == Form.admin_keybuy_banner.state:
        if not is_admin(m.from_user.id):
            await state.clear()
            return
        if not m.photo:
            if text.lower() == "/cancel":
                await state.clear()
                await m.answer("❌ Payment photo change cancelled.", reply_markup=back_admin())
            else:
                await m.answer("📸 Please send an image as a Telegram photo, or /cancel.")
            return
        banner_path = os.path.join(BASE_DIR, "payment_banner.png")
        try:
            await bot.download(m.photo[-1].file_id, destination=banner_path)
            await state.clear()
            await m.answer("✅ Payment photo updated! The new photo will appear above the KEY BUY payment details.", reply_markup=back_admin())
        except Exception as e:
            log.exception("Payment banner upload failed: %r", e)
            await m.answer("❌ Could not save that photo. Please try sending it again.")
        return

    # IMPORTANT: this catch-all message router is registered before the
    # state-specific handlers below.  Therefore stateful inputs must be
    # handled here explicitly, otherwise aiogram stops at this handler and
    # the later Form.* handlers never receive the message.
    if current_state == Form.keybuy_txid.state:
        # Reply-keyboard navigation must remain usable while waiting for a TXID.
        nav_texts = {
            get_setting("get_number", "📞 GET NUMBER"),
            get_setting("countries", "🌍 AVAILABLE COUNTRY"),
            "🔑 KEY BUY",
            get_setting("admin_button", "👑 ADMIN PANEL"),
        }
        if text.lower() == "/cancel" or text in nav_texts:
            await state.clear()
            current_state = None
            await m.answer("↩️ KEY BUY submission cancelled. You can use the menu now.", reply_markup=user_keyboard(m.from_user.id))
            if text.lower() == "/cancel":
                return
            # Continue below to process the tapped menu button normally.
        else:
            txid = text
            if not re.fullmatch(r"0x[a-fA-F0-9]{64}", txid):
                await m.answer("❌ <b>Invalid TXID format.</b>\n\nBEP-20 (BSC) needs <code>0x</code> + 64 hex characters. Please paste the full transaction hash, or tap a menu button to leave this step.")
                return
            c = conn()
            duplicate = c.execute("SELECT id FROM key_buy_requests WHERE txid=?", (txid,)).fetchone()
            if duplicate:
                c.close()
                await state.clear()
                await m.answer("⚠️ This TXID has already been submitted.", reply_markup=user_keyboard(m.from_user.id))
                return
            pending = c.execute("SELECT id FROM key_buy_requests WHERE user_id=? AND status='pending' LIMIT 1", (m.from_user.id,)).fetchone()
            if pending:
                c.close()
                await state.clear()
                await m.answer("⏳ You already have a pending KEY BUY request.", reply_markup=user_keyboard(m.from_user.id))
                return
            c.execute("INSERT INTO key_buy_requests(user_id,username,txid,status,created_at) VALUES(?,?,?,?,?)",
                      (m.from_user.id, m.from_user.username or "", txid, "pending", datetime.now().isoformat()))
            c.commit()
            request_id = c.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            c.close()
            await state.clear()
            await m.answer("⏳ <b>PAYMENT PENDING</b>\n\nYour TXID has been submitted for admin review.\nAfter approval, the key will be delivered here.", reply_markup=user_keyboard(m.from_user.id))
            try:
                await bot.send_message(ADMIN_ID,
                    f"🛒 <b>NEW KEY BUY REQUEST #{request_id}</b>\n\n"
                    f"👤 User: {('@' + html.escape(m.from_user.username)) if m.from_user.username else 'No username'}\n"
                    f"🆔 ID: <code>{m.from_user.id}</code>\n"
                    f"🧾 TXID:\n<code>{html.escape(txid)}</code>",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👀 REVIEW", callback_data=f"adm:keybuy:view:{request_id}")]]))
            except Exception as e:
                log.warning("Could not notify admin about KEY BUY request %s: %r", request_id, e)
            return

    if current_state:
        # Normal FSM handlers should catch this first. If a runtime/version
        # mismatch prevents that, handle admin input here instead of silently
        # dropping the message.
        if is_admin(m.from_user.id) and await handle_admin_text_by_state(m, state, current_state):
            return
        return
    text = (m.text or "").strip()
    if text == "🔑 KEY BUY":
        await key_buy_menu(m, state)
        return
    if text == get_setting("get_number", "📞 GET NUMBER"):
        # Remove the persistent main keyboard first, then show the inline service menu.
        await m.answer(label_text("select_service", "icon_select_service", "📱"),
                       reply_markup=service_keyboard())
        return
    if text == get_setting("countries", "🌍 TRAFFIC COUNTRY"):
        c = conn(); rows = c.execute("SELECT code,name,flag FROM countries WHERE active=1 ORDER BY CASE code WHEN 'BD' THEN 1 WHEN 'IN' THEN 2 WHEN 'PK' THEN 3 ELSE 4 END, name").fetchall(); c.close()
        lines=[f"<b>{html.escape(get_setting('traffic_title','📊 Live Traffic Update'))}</b>", ""]
        for r in rows:
            level=get_setting('traffic_'+r['code'], 'High').strip().title()
            if level not in ('High','Low','Medium'): level='High'
            dot={'High':'🟢','Medium':'🟡','Low':'🔴'}[level]
            lines.append(f"{r['flag']} {html.escape(r['name'])} <b>{level}</b> {dot}")
        await m.answer("\n".join(lines) if rows else "No countries available.")
        return
    if is_admin(m.from_user.id) and text == get_setting("admin_button", "👑 ADMIN PANEL"):
        await m.answer("👑 <b>DRAGON SMS ADMIN PANEL</b>\n\nPrivate control center", reply_markup=admin_keyboard())
        return


@dp.callback_query(F.data.startswith("svc:"))
async def choose_service(q: CallbackQuery):
    code = q.data.split(":", 1)[1]
    c = conn(); service = c.execute("SELECT name,emoji,custom_emoji_id FROM services WHERE code=? AND active=1", (code,)).fetchone()
    countries = c.execute("SELECT code,name,flag,prefix,custom_emoji_id FROM countries WHERE active=1 ORDER BY name").fetchall(); c.close()
    if not service: await q.answer("Service unavailable.", show_alert=True); return
    # Render one country flag only (avoid custom button icon + text flag duplication).
    country_buttons = [InlineKeyboardButton(text=f"{r['flag']} {r['name']}", callback_data=f"country:{code}:{r['code']}") for r in countries]
    buttons = [country_buttons[i:i+2] for i in range(0, len(country_buttons), 2)]
    buttons.append([icon_button(get_setting("icon_back", ""), "BACK", callback_data="back_services")])
    await q.message.edit_text(label_text("select_country", "icon_select_country", "🌍"), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await q.answer()


@dp.callback_query(F.data == "back_services")
async def back_services(q: CallbackQuery):
    # Leaving the number/service flow must cancel any scheduled demo OTP.
    cancel_otp(q.message.chat.id)
    await q.answer()
    await q.message.edit_text(label_text("select_service", "icon_select_service", "📱"), reply_markup=service_keyboard())


@dp.callback_query(F.data == "back_main")
async def back_main(q: CallbackQuery):
    # Leaving the number/service flow must cancel any scheduled demo OTP.
    cancel_otp(q.message.chat.id)
    # Leave the service flow completely and restore the main reply keyboard.
    await q.answer()
    try:
        await q.message.delete()
    except Exception:
        pass
    await q.message.answer(welcome_text(q.from_user), reply_markup=user_keyboard(q.from_user.id))


def demo_number(prefix: str) -> str:
    return prefix + "".join(str(random.randrange(10)) for _ in range(10))


def demo_otp(service_name: str = "") -> str:
    # WhatsApp uses the existing 6-digit demo OTP; Telegram uses 5 digits.
    if service_name.strip().lower() == "telegram":
        return f"{random.randrange(10000, 100000):05d}"
    return f"{random.randrange(100,1000):03d}-{random.randrange(100,1000):03d}"


def cancel_otp(chat_id: int):
    task = OTP_TASKS.pop(chat_id, None)
    if task and not task.done(): task.cancel()
    ACTIVE_SESSIONS.pop(chat_id, None)


async def otp_worker(chat_id: int, session_id: str, number: str, otp: str, service_name: str):
    try:
        await asyncio.sleep(60)
        session = ACTIVE_SESSIONS.get(chat_id)
        if not session or session["id"] != session_id or session["number"] != number:
            return
        await bot.send_message(
            chat_id,
            f"{custom_emoji(get_setting('icon_otp_title', ""), "✉️")} {get_setting('otp_title', 'New DID Received!').lstrip('✉️ ')}\n\n"
            f"Number: <code>{html.escape(number)}</code>\n"
            f"OTP: <code>{html.escape(otp)}</code>\n"
            f"Service: {html.escape(service_name)}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=otp, copy_text=CopyTextButton(text=otp))
            ]])
        )
    except asyncio.CancelledError:
        return
    except Exception as e:
        log.warning("OTP send failed: %r", e)


async def deliver_number(chat_id: int, service_code: str, country_code: str, edit_message: Message | None = None):
    c = conn()
    service = c.execute("SELECT name,emoji,custom_emoji_id FROM services WHERE code=? AND active=1", (service_code,)).fetchone()
    country = c.execute("SELECT name,prefix,flag,custom_emoji_id FROM countries WHERE code=? AND active=1", (country_code,)).fetchone()
    c.close()
    if not service or not country:
        if edit_message:
            await edit_message.answer("❌ Service or country is no longer available.")
        else:
            await bot.send_message(chat_id, "❌ Service or country is no longer available.")
        return

    cancel_otp(chat_id)
    number = demo_number(country["prefix"])
    otp = demo_otp(service["name"])
    # Keep a permanent history of generated demo numbers per Telegram user.
    c = conn()
    urow = c.execute("SELECT username FROM users WHERE user_id=?", (chat_id,)).fetchone()
    uname = urow["username"] if urow else ""
    c.execute(
        "INSERT INTO user_numbers(user_id,username,service,country,number,created_at) VALUES(?,?,?,?,?,?)",
        (chat_id, uname, service["name"], country["name"], number, datetime.now().isoformat()),
    )
    c.commit(); c.close()
    session_id = secrets.token_hex(8)
    ACTIVE_SESSIONS[chat_id] = {"id": session_id, "number": number, "otp": otp}
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=number, copy_text=CopyTextButton(text=number))],
        [
            InlineKeyboardButton(text="🔄 NEW NUMBER", callback_data=f"country:{service_code}:{country_code}"),
            icon_button(get_setting("icon_back", ""), "BACK", callback_data="back_services"),
        ],
    ])
    text = (
        f"{label_text('number_header', 'icon_number_header', '📲')}\n\n"
        f"{custom_emoji(get_setting('icon_service_label', ''), '🔹')} Service: {html.escape(service['name'])}\n"
        f"{custom_emoji(get_setting('icon_country_label', ''), '🌐')} Country: {custom_emoji(country['custom_emoji_id'], country['flag'])} {html.escape(country['name'])} ({country['prefix']})\n\n"
        f"{custom_emoji(get_setting('icon_number_label', ''), '📞')} Number: <code>{number}</code>\n\n"
        f"{get_setting('waiting_text', '⏳ WAITING FOR OTP...........................')}"
    )
    if edit_message:
        await edit_message.edit_text(text, reply_markup=markup)
    else:
        await bot.send_message(chat_id, "⁣", reply_markup=ReplyKeyboardRemove())
        await bot.send_message(chat_id, text, reply_markup=markup)
    OTP_TASKS[chat_id] = asyncio.create_task(otp_worker(chat_id, session_id, number, otp, service["name"]))

@dp.callback_query(F.data.startswith("country:"))
async def choose_country(q: CallbackQuery, state: FSMContext):
    _, service_code, country_code = q.data.split(":", 2)
    if not await is_user_verified(q.from_user.id):
        await state.update_data(pending_service_code=service_code, pending_country_code=country_code)
        await state.set_state(Form.waiting_key)
        await q.answer()
        await q.message.answer(f"{custom_emoji(get_setting('icon_access_key', ""), "🔑")} <b>Please enter your access key.</b>\n\n{custom_emoji(get_setting('icon_warning', ""), "⚠️")} A valid key is required before this number can be shown.")
        return
    await q.answer()
    await deliver_number(q.message.chat.id, service_code, country_code, edit_message=q.message)


async def is_user_verified(uid: int) -> bool:
    """Trust verification only while the user's linked access key is active."""
    c = conn()
    row = c.execute("SELECT verified, verified_key FROM users WHERE user_id=?", (uid,)).fetchone()
    if not row or not row["verified"] or not row["verified_key"]:
        c.close()
        return False
    keyrow = c.execute(
        "SELECT 1 FROM access_keys WHERE key=? AND owner_id=? AND active=1 LIMIT 1",
        (row["verified_key"], uid),
    ).fetchone()
    if not keyrow:
        # Repair stale cached verification after an admin disables/unlinks a key.
        c.execute("UPDATE users SET verified=0, verified_key=NULL WHERE user_id=?", (uid,))
        c.commit()
    c.close()
    return bool(keyrow)



# =========================================================
# KEY BUY / MANUAL BEP-20 PAYMENT REVIEW
# =========================================================

def key_buy_keyboard():
    address = get_setting("key_buy_address", "")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 I PAID — SEND TXID", callback_data="keybuy:paid")],
        [InlineKeyboardButton(text="❌ CANCEL", callback_data="keybuy:cancel")],
    ])


def key_buy_text():
    amount = get_setting("key_buy_amount", "1.00")
    address = get_setting("key_buy_address", "")
    return (
        f"💵 <b>BUY KEY</b>\n\n"
        f"Send <b>${html.escape(amount)} USDT</b>\n"
        f"🌐 Network: <b>BEP-20 (BSC)</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📥 <b>Address (tap to copy):</b>\n"
        f"<code>{html.escape(address)}</code>\n\n"
        f"❌ <b>BEP-20 (BSC) only</b> — wrong network may result in loss of funds.\n"
        f"\nAfter sending the payment, tap <b>I PAID — SEND TXID</b> and paste the full transaction hash."
    )


@dp.message(F.text == "🔑 KEY BUY")
async def key_buy_menu(m: Message, state: FSMContext):
    await state.clear()
    if get_setting("key_buy_enabled", "1") != "1":
        await m.answer("⛔ KEY BUY is currently disabled by admin.", reply_markup=user_keyboard(m.from_user.id))
        return
    if user_has_completed_key_buy(m.from_user.id):
        await m.answer("✅ You already purchased a key.", reply_markup=user_keyboard(m.from_user.id))
        return
    c = conn()
    pending = c.execute("SELECT id FROM key_buy_requests WHERE user_id=? AND status='pending' LIMIT 1", (m.from_user.id,)).fetchone()
    c.close()
    if pending:
        await m.answer("⏳ <b>Your KEY BUY request is already pending.</b>\n\nPlease wait for admin approval.", reply_markup=user_keyboard(m.from_user.id))
        return
    banner_path = os.path.join(BASE_DIR, "payment_banner.png")
    if os.path.isfile(banner_path):
        await m.answer_photo(FSInputFile(banner_path), caption=key_buy_text(), reply_markup=key_buy_keyboard())
    else:
        await m.answer(key_buy_text(), reply_markup=key_buy_keyboard())


@dp.callback_query(F.data == "keybuy:paid")
async def keybuy_paid(q: CallbackQuery, state: FSMContext):
    if get_setting("key_buy_enabled", "1") != "1":
        await q.answer("KEY BUY is currently disabled.", show_alert=True)
        return
    if user_has_completed_key_buy(q.from_user.id):
        await q.answer("You already purchased a key.", show_alert=True)
        return
    c = conn()
    pending = c.execute("SELECT id FROM key_buy_requests WHERE user_id=? AND status='pending' LIMIT 1", (q.from_user.id,)).fetchone()
    c.close()
    if pending:
        await q.answer("Your payment is already pending review.", show_alert=True)
        return
    await state.set_state(Form.keybuy_txid)
    await q.message.answer(
        "🧾 <b>PASTE YOUR BEP-20 (BSC) TXID</b>\n\n"
        "The transaction hash must be <code>0x</code> + 64 hexadecimal characters.\n\n"
        "Example:\n<code>0x1234...abcd</code>"
    )
    await q.answer()


@dp.message(Form.keybuy_txid)
async def keybuy_txid_receive(m: Message, state: FSMContext):
    txid = (m.text or "").strip()
    import re
    if not re.fullmatch(r"0x[a-fA-F0-9]{64}", txid):
        await m.answer("❌ <b>Invalid TXID format.</b>\n\nBEP-20 (BSC) needs <code>0x</code> + 64 hex characters. Please paste the full transaction hash.")
        return
    c = conn()
    duplicate = c.execute("SELECT id FROM key_buy_requests WHERE txid=?", (txid,)).fetchone()
    if duplicate:
        c.close()
        await state.clear()
        await m.answer("⚠️ This TXID has already been submitted.", reply_markup=user_keyboard(m.from_user.id))
        return
    pending = c.execute("SELECT id FROM key_buy_requests WHERE user_id=? AND status='pending' LIMIT 1", (m.from_user.id,)).fetchone()
    if pending:
        c.close(); await state.clear()
        await m.answer("⏳ You already have a pending KEY BUY request.", reply_markup=user_keyboard(m.from_user.id))
        return
    c.execute(
        "INSERT INTO key_buy_requests(user_id,username,txid,status,created_at) VALUES(?,?,?,?,?)",
        (m.from_user.id, m.from_user.username or "", txid, "pending", datetime.now().isoformat())
    )
    c.commit(); c.close(); await state.clear()
    await m.answer(
        "⏳ <b>PAYMENT PENDING</b>\n\n"
        "Your TXID has been submitted for admin review.\n"
        "After approval, the key will be delivered here.",
        reply_markup=user_keyboard(m.from_user.id)
    )
    cc = conn(); rr = cc.execute("SELECT id FROM key_buy_requests WHERE user_id=? AND txid=?", (m.from_user.id, txid)).fetchone(); cc.close()
    if rr:
        await bot.send_message(
            ADMIN_ID,
            f"🛒 <b>NEW KEY BUY REQUEST #{rr['id']}</b>\n\n"
            f"👤 User: {('@' + html.escape(m.from_user.username)) if m.from_user.username else 'No username'}\n"
            f"🆔 ID: <code>{m.from_user.id}</code>\n"
            f"🧾 TXID:\n<code>{html.escape(txid)}</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="👀 REVIEW", callback_data=f"adm:keybuy:view:{rr['id']}")]
            ])
        )


@dp.callback_query(F.data == "keybuy:cancel")
async def keybuy_cancel(q: CallbackQuery, state: FSMContext):
    await state.clear()
    await q.answer("Cancelled.")
    try:
        await q.message.delete()
    except Exception:
        pass
    await q.message.answer(welcome_text(q.from_user), reply_markup=user_keyboard(q.from_user.id))


# =========================================================
# ADMIN KEY BUY REVIEW
# =========================================================
@dp.callback_query(F.data == "adm:keybuy")
async def adm_keybuy(q: CallbackQuery):
    if not await admin_only(q): return
    await render_keybuy_admin(q)


async def render_keybuy_admin(q: CallbackQuery):
    c = conn()
    pending = c.execute("SELECT COUNT(*) n FROM key_buy_requests WHERE status='pending'").fetchone()["n"]
    rows = c.execute("SELECT id,user_id,username,txid,created_at FROM key_buy_requests WHERE status='pending' ORDER BY id DESC LIMIT 20").fetchall()
    c.close()
    lines = ["🛒 <b>KEY BUY REQUESTS</b>", "", f"⏳ Pending: <b>{pending}</b>", ""]
    buttons=[]
    for r in rows:
        uname = f"@{html.escape(r['username'])}" if r['username'] else f"ID {r['user_id']}"
        lines.append(f"#{r['id']} — {uname}")
        buttons.append([InlineKeyboardButton(text=f"#{r['id']} • {r['username'] or r['user_id']}", callback_data=f"adm:keybuy:view:{r['id']}")])
    if not rows: lines.append("No pending requests.")
    buttons.append([InlineKeyboardButton(text="📚 BUY HISTORY", callback_data="adm:keybuy:history")])
    enabled = get_setting("key_buy_enabled", "1") == "1"
    buttons.append([InlineKeyboardButton(text=("⛔ DISABLE KEY BUY" if enabled else "✅ ENABLE KEY BUY"), callback_data="adm:keybuy:toggle")])
    buttons.append([InlineKeyboardButton(text="⚙️ KEY BUY SETTINGS", callback_data="adm:keybuy:settings")])
    buttons.append([InlineKeyboardButton(text="⬅️ ADMIN PANEL", callback_data="adm:home")])
    await q.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await q.answer()


@dp.callback_query(F.data.startswith("adm:keybuy:view:"))
async def adm_keybuy_view(q: CallbackQuery):
    if not await admin_only(q): return
    rid=int(q.data.rsplit(":",1)[1])
    c=conn(); r=c.execute("SELECT * FROM key_buy_requests WHERE id=?",(rid,)).fetchone(); c.close()
    if not r:
        await q.answer("Request not found.", show_alert=True); return
    uname=f"@{html.escape(r['username'])}" if r['username'] else "No username"
    status=r['status'].upper()
    text=(f"🛒 <b>KEY BUY REQUEST #{rid}</b>\n\n"
          f"👤 Username: {uname}\n"
          f"🆔 Telegram ID: <code>{r['user_id']}</code>\n\n"
          f"🧾 <b>TXID</b>\n<code>{html.escape(r['txid'])}</code>\n\n"
          f"Status: <b>{status}</b>")
    buttons=[]
    if r['status']=='pending':
        buttons.append([InlineKeyboardButton(text="✅ APPROVE", callback_data=f"adm:keybuy:approve:{rid}"), InlineKeyboardButton(text="❌ REJECT", callback_data=f"adm:keybuy:reject:{rid}")])
    elif r['status']=='approved':
        c2=conn(); keyrow=c2.execute("SELECT key,active FROM access_keys WHERE owner_id=? ORDER BY created_at DESC LIMIT 1", (r['user_id'],)).fetchone(); c2.close()
        if keyrow:
            action = "enable" if not keyrow['active'] else "disable"
            label = "✅ ENABLE USER KEY" if action == "enable" else "⛔ DISABLE USER KEY"
            buttons.append([InlineKeyboardButton(text=label, callback_data=f"adm:keybuy:userkey:{action}:{r['user_id']}")])
    buttons.append([InlineKeyboardButton(text="⬅️ KEY BUY REQUESTS", callback_data="adm:keybuy")])
    await q.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)); await q.answer()


@dp.callback_query(F.data.startswith("adm:keybuy:approve:"))
async def adm_keybuy_approve(q: CallbackQuery, state: FSMContext):
    if not await admin_only(q): return
    rid=int(q.data.rsplit(":",1)[1])
    c=conn(); r=c.execute("SELECT * FROM key_buy_requests WHERE id=? AND status='pending'",(rid,)).fetchone(); c.close()
    if not r:
        await q.answer("Request is no longer pending.", show_alert=True); return
    await state.update_data(keybuy_request_id=rid)
    await state.set_state(Form.admin_keybuy_key)
    await q.message.answer(f"✅ <b>APPROVE REQUEST #{rid}</b>\n\nSend the exact KEY you want to deliver to {('@' + html.escape(r['username'])) if r['username'] else 'the user'}.")
    await q.answer()


@dp.message(Form.admin_keybuy_key)
async def admin_keybuy_key_receive(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    key=(m.text or "").strip()
    if len(key)<3 or "|" in key:
        await m.answer("❌ Invalid key. Send the exact key to deliver."); return
    data=await state.get_data(); rid=data.get("keybuy_request_id")
    c=conn(); r=c.execute("SELECT * FROM key_buy_requests WHERE id=? AND status='pending'",(rid,)).fetchone()
    if not r:
        c.close(); await state.clear(); await m.answer("❌ Request is no longer pending.", reply_markup=back_admin()); return
    existing=c.execute("SELECT key FROM access_keys WHERE key=?",(key,)).fetchone()
    if existing:
        c.close(); await m.answer("⚠️ This key already exists in Key Management. Send a different key."); return
    c.execute("INSERT INTO access_keys(key,owner_id,active,created_at) VALUES(?,?,1,?)",(key,r['user_id'],datetime.now().isoformat()))
    c.execute("UPDATE users SET verified=0,verified_key=NULL,joined=1 WHERE user_id=?",(r['user_id'],))
    c.execute("UPDATE key_buy_requests SET status='approved',admin_key=?,reviewed_at=? WHERE id=?",(key,datetime.now().isoformat(),rid))
    c.commit(); c.close(); await state.clear()
    try:
        await bot.send_message(r['user_id'], f"✅ <b>PAYMENT SUCCESSFUL</b>\n\n🔑 Your Key:\n<code>{html.escape(key)}</code>\n\nPayment approved. Send this key to the bot to verify your Telegram ID and unlock number access.", reply_markup=user_keyboard(r['user_id']))
    except Exception as e:
        log.warning("Could not notify buyer %s: %r", r['user_id'], e)
    await m.answer(f"✅ <b>REQUEST #{rid} APPROVED</b>\n\nKey delivered to the user.", reply_markup=back_admin())


@dp.callback_query(F.data.startswith("adm:keybuy:userkey:"))
async def adm_keybuy_userkey_toggle(q: CallbackQuery):
    if not await admin_only(q): return
    parts=q.data.split(":")
    action=parts[3]; uid=int(parts[4])
    c=conn()
    row=c.execute("SELECT key FROM access_keys WHERE owner_id=? ORDER BY created_at DESC LIMIT 1", (uid,)).fetchone()
    if not row:
        c.close(); await q.answer("No linked key found.", show_alert=True); return
    key=row['key']; active=1 if action=="enable" else 0
    c.execute("UPDATE access_keys SET active=? WHERE key=?", (active,key))
    if active:
        c.execute("UPDATE users SET verified=1, verified_key=? WHERE user_id=?", (key,uid))
    else:
        c.execute("UPDATE users SET verified=0, verified_key=NULL WHERE user_id=?", (uid,))
    c.commit(); c.close()
    try:
        await bot.send_message(uid, "✅ Your access key has been enabled. Number access is restored." if active else "⛔ Your access key has been disabled. Please use KEY BUY to submit a new request.", reply_markup=user_keyboard(uid))
    except Exception as e:
        log.warning("Could not notify user key status change %s: %r", uid, e)
    await q.answer("User key enabled." if active else "User key disabled.")
    await q.message.answer(f"User {uid} key status: {'ENABLED' if active else 'DISABLED'}", reply_markup=back_admin())


@dp.callback_query(F.data.startswith("adm:keybuy:reject:"))
async def adm_keybuy_reject(q: CallbackQuery):
    if not await admin_only(q): return
    rid=int(q.data.rsplit(":",1)[1])
    c=conn(); r=c.execute("SELECT * FROM key_buy_requests WHERE id=? AND status='pending'",(rid,)).fetchone()
    if not r:
        c.close(); await q.answer("Request is no longer pending.", show_alert=True); return
    c.execute("UPDATE key_buy_requests SET status='rejected',reviewed_at=? WHERE id=?",(datetime.now().isoformat(),rid)); c.commit(); c.close()
    try:
        await bot.send_message(r['user_id'], "❌ <b>PAYMENT REJECTED</b>\n\nYour KEY BUY request was rejected by admin. You can submit a new request if needed.", reply_markup=user_keyboard(r['user_id']))
    except Exception as e:
        log.warning("Could not notify rejected buyer: %r", e)
    await q.answer("Request rejected.")
    await render_keybuy_admin(q)


@dp.callback_query(F.data == "adm:keybuy:history")
async def adm_keybuy_history(q: CallbackQuery):
    if not await admin_only(q): return
    c=conn(); rows=c.execute("SELECT id,user_id,username,txid,status,created_at FROM key_buy_requests ORDER BY id DESC LIMIT 50").fetchall(); c.close()
    lines=["📚 <b>KEY BUY HISTORY</b>",""]
    buttons=[]
    for r in rows:
        uname=f"@{html.escape(r['username'])}" if r['username'] else str(r['user_id'])
        icon={"pending":"⏳","approved":"✅","rejected":"❌"}.get(r['status'],"•")
        lines.append(f"{icon} #{r['id']} — {uname} — <b>{r['status'].upper()}</b>")
        buttons.append([InlineKeyboardButton(text=f"#{r['id']} • {r['status'].upper()}", callback_data=f"adm:keybuy:view:{r['id']}")])
    if not rows: lines.append("No KEY BUY submissions yet.")
    buttons.append([InlineKeyboardButton(text="⬅️ KEY BUY", callback_data="adm:keybuy")])
    await q.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)); await q.answer()


@dp.callback_query(F.data == "adm:keybuy:toggle")
async def adm_keybuy_toggle(q: CallbackQuery):
    if not await admin_only(q): return
    new_value = "0" if get_setting("key_buy_enabled", "1") == "1" else "1"
    set_setting("key_buy_enabled", new_value)
    await render_keybuy_admin(q)


@dp.callback_query(F.data == "adm:keybuy:settings")
async def adm_keybuy_settings(q: CallbackQuery):
    if not await admin_only(q): return
    amount=get_setting("key_buy_amount","1.00"); address=get_setting("key_buy_address","")
    await q.message.edit_text(
        "⚙️ <b>KEY BUY SETTINGS</b>\n\n"
        f"💵 Amount: <code>${html.escape(amount)} USDT</code>\n"
        f"📥 BEP-20 Address:\n<code>{html.escape(address)}</code>\n\n"
        f"🛒 KEY BUY system: <b>{'ENABLED' if get_setting('key_buy_enabled','1') == '1' else 'DISABLED'}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📥 CHANGE ADDRESS", callback_data="adm:keybuy:setaddr")],
            [InlineKeyboardButton(text="💵 CHANGE PRICE", callback_data="adm:keybuy:setprice")],
            [InlineKeyboardButton(text="🖼️ CHANGE PAYMENT PHOTO", callback_data="adm:keybuy:setbanner")],
            [InlineKeyboardButton(text="⬅️ KEY BUY", callback_data="adm:keybuy")],
        ])
    ); await q.answer()


@dp.callback_query(F.data == "adm:keybuy:setaddr")
async def adm_keybuy_setaddr(q: CallbackQuery, state: FSMContext):
    if not await admin_only(q): return
    await state.set_state(Form.admin_keybuy_address)
    await q.message.answer("📥 Send the new BEP-20 (BSC) address:"); await q.answer()


@dp.callback_query(F.data == "adm:keybuy:setbanner")
async def adm_keybuy_setbanner(q: CallbackQuery, state: FSMContext):
    if not await admin_only(q): return
    await state.set_state(Form.admin_keybuy_banner)
    await q.message.answer(
        "🖼️ <b>CHANGE PAYMENT PHOTO</b>\n\n"
        "Now send the photo you want displayed at the top of the KEY BUY payment screen.\n"
        "Send it as a photo (not as a file/document). Send /cancel to cancel."
    )
    await q.answer()


@dp.callback_query(F.data == "adm:keybuy:setprice")
async def adm_keybuy_setprice(q: CallbackQuery, state: FSMContext):
    if not await admin_only(q): return
    await state.set_state(Form.admin_keybuy_price)
    await q.message.answer("💵 Send the new price in USDT, e.g. <code>1.00</code>"); await q.answer()


@dp.message(Form.admin_keybuy_address)
async def admin_keybuy_address_receive(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    value=(m.text or "").strip()
    import re
    if not re.fullmatch(r"0x[a-fA-F0-9]{40}", value):
        await m.answer("❌ Invalid BEP-20 address. It must be 0x + 40 hexadecimal characters.")
        return
    set_setting("key_buy_address", value)
    await state.clear()
    await m.answer("✅ BEP-20 address updated.", reply_markup=back_admin())


@dp.message(Form.admin_keybuy_price)
async def admin_keybuy_price_receive(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    value=(m.text or "").strip()
    import re
    if not re.fullmatch(r"(?:\d+)(?:\.\d{1,2})?", value) or float(value) <= 0:
        await m.answer("❌ Invalid price. Example: 1.00")
        return
    set_setting("key_buy_amount", f"{float(value):.2f}")
    await state.clear()
    await m.answer("✅ Key price updated.", reply_markup=back_admin())


# =========================================================
# ADMIN BROADCAST
# =========================================================
@dp.callback_query(F.data == "adm:broadcast")
async def adm_broadcast(q: CallbackQuery, state: FSMContext):
    if not await admin_only(q): return
    await state.set_state(Form.admin_broadcast)
    await q.message.answer(
        "📣 <b>BROADCAST</b>\n\n"
        "Send the message you want to broadcast.\n"
        "You can send <b>text, photo, video, document, animation, audio</b>, etc.\n\n"
        "After sending it, I will ask whether you want to add a clickable URL button.\n\n"
        "Send <code>/cancel</code> to cancel."
    ); await q.answer()


@dp.message(Form.admin_broadcast)
async def adm_broadcast_receive(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    if (m.text or "").strip().lower()=="/cancel":
        await state.clear(); await m.answer("❌ Broadcast cancelled.", reply_markup=back_admin()); return
    await state.update_data(broadcast_chat_id=m.chat.id, broadcast_message_id=m.message_id)
    await state.set_state(Form.admin_broadcast_button)
    await m.answer(
        "🔗 <b>ADD A BUTTON?</b>\n\n"
        "To add a clickable button, send:\n"
        "<code>Button Text|https://example.com</code>\n\n"
        "Or send <code>/skip</code> to broadcast without an extra button.\n"
        "Send <code>/cancel</code> to cancel."
    )


@dp.message(Form.admin_broadcast_button)
async def adm_broadcast_button_receive(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    text=(m.text or "").strip()
    if text.lower()=="/cancel":
        await state.clear(); await m.answer("❌ Broadcast cancelled.", reply_markup=back_admin()); return
    markup=None
    if text.lower() != "/skip":
        parts=text.split("|",1)
        if len(parts)!=2 or not parts[0].strip() or not parts[1].strip().startswith(("https://","http://")):
            await m.answer("❌ Format: <code>Button Text|https://example.com</code> or send <code>/skip</code>.")
            return
        markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=parts[0].strip(), url=parts[1].strip())]])
    data=await state.get_data(); source_chat=data.get("broadcast_chat_id"); source_message=data.get("broadcast_message_id")
    c=conn(); users=c.execute("SELECT user_id FROM users").fetchall(); c.close()
    sent=0; failed=0
    for row in users:
        uid=row["user_id"]
        try:
            await bot.copy_message(chat_id=uid, from_chat_id=source_chat, message_id=source_message, reply_markup=markup)
            sent+=1
        except Exception as e:
            failed+=1
            log.warning("Broadcast failed for %s: %r", uid, e)
        await asyncio.sleep(0.04)
    await state.clear()
    await m.answer(f"📣 <b>BROADCAST COMPLETE</b>\n\n✅ Sent: {sent}\n❌ Failed: {failed}", reply_markup=back_admin())

# =========================================================
# ADMIN PANEL
# =========================================================
async def admin_only(q_or_m):
    if not is_admin(q_or_m.from_user.id):
        if isinstance(q_or_m, CallbackQuery): await q_or_m.answer("⛔ Admin only.", show_alert=True)
        return False
    return True


@dp.message(Command("admin"))
async def admin_command(m: Message):
    if is_admin(m.from_user.id): await m.answer("👑 <b>DRAGON SMS ADMIN PANEL</b>\n\nPrivate control center", reply_markup=admin_keyboard())


@dp.message(Command("panel"))
async def panel_command(m: Message):
    if is_admin(m.from_user.id): await m.answer("👑 <b>ADMIN PANEL</b>", reply_markup=admin_keyboard())


@dp.callback_query(F.data == "adm:home")
async def adm_home(q: CallbackQuery):
    if await admin_only(q): await q.message.edit_text("👑 <b>DRAGON SMS ADMIN PANEL</b>\n\nManage every major bot setting from here.", reply_markup=admin_keyboard())


@dp.callback_query(F.data == "adm:control")
async def adm_control(q: CallbackQuery, state: FSMContext):
    if not await admin_only(q): return
    enabled = get_setting("bot_enabled", "1") == "1"
    status = "🟢 RUNNING" if enabled else "⛔ STOPPED"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=("⛔ STOP BOT" if enabled else "🟢 START BOT"), callback_data="adm:control:toggle")],
        [InlineKeyboardButton(text="✏️ EDIT STOP MESSAGE", callback_data="adm:control:message")],
        [InlineKeyboardButton(text="⬅️ ADMIN PANEL", callback_data="adm:home")],
    ])
    await q.message.edit_text(
        f"⚙️ <b>BOT CONTROL</b>\n\nCurrent status: <b>{status}</b>\n\n"
        "When stopped, non-admin users receive your configured message.", reply_markup=kb)
    await q.answer()


@dp.callback_query(F.data == "adm:control:toggle")
async def adm_control_toggle(q: CallbackQuery):
    if not await admin_only(q): return
    currently_enabled = get_setting("bot_enabled", "1") == "1"
    set_setting("bot_enabled", "0" if currently_enabled else "1")
    enabled = not currently_enabled
    status = "🟢 RUNNING" if enabled else "⛔ STOPPED"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=("⛔ STOP BOT" if enabled else "🟢 START BOT"), callback_data="adm:control:toggle")],
        [InlineKeyboardButton(text="✏️ EDIT STOP MESSAGE", callback_data="adm:control:message")],
        [InlineKeyboardButton(text="⬅️ ADMIN PANEL", callback_data="adm:home")],
    ])
    await q.message.edit_text(f"⚙️ <b>BOT CONTROL</b>\n\nCurrent status: <b>{status}</b>", reply_markup=kb)
    await q.answer("Bot started." if enabled else "Bot stopped.")


@dp.callback_query(F.data == "adm:control:message")
async def adm_control_message(q: CallbackQuery, state: FSMContext):
    if not await admin_only(q): return
    await state.set_state(Form.admin_stopped_message)
    current = get_setting("bot_stopped_message", "⛔ <b>BOT IS CURRENTLY STOPPED</b>\n\nPlease try again later.")
    await q.message.answer(
        "✏️ Send the message users should receive while the bot is stopped.\n"
        "HTML formatting is supported. Send /cancel to cancel.\n\n"
        f"Current message:\n{current}")
    await q.answer()


@dp.message(Form.admin_stopped_message)
async def adm_control_message_receive(m: Message, state: FSMContext):
    if not await admin_only(m): return
    if (m.text or "").strip().lower() == "/cancel":
        await state.clear()
        await m.answer("Cancelled.", reply_markup=admin_keyboard())
        return
    if not m.text or len(m.text) > 3500:
        await m.answer("Please send a text message under 3500 characters.")
        return
    set_setting("bot_stopped_message", m.text)
    await state.clear()
    await m.answer("✅ Stop message updated.", reply_markup=admin_keyboard())


@dp.callback_query(F.data == "adm:backup")
async def adm_backup(q: CallbackQuery):
    if not await admin_only(q): return
    await q.answer("Preparing backup…")
    # Safe backup/export only: uploaded Python ZIPs are never executed automatically.
    fd, archive_path = tempfile.mkstemp(prefix="dragon_sms_backup_", suffix=".zip")
    os.close(fd)
    try:
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in ("bot.py", "requirements.txt", "README.txt", "payment_banner.png", "tiger_flow.sqlite3"):
                path = os.path.join(BASE_DIR, name)
                if os.path.isfile(path):
                    archive.write(path, arcname=name)
        await q.message.answer_document(
            FSInputFile(archive_path, filename="DRAGON_SMS_BACKUP.zip"),
            caption="💾 <b>DRAGON SMS BACKUP</b>\nContains available bot files and database. Store it privately.\n\nFor updates, review the code and deploy manually; ZIP uploads are not auto-executed for security.")
    finally:
        try: os.remove(archive_path)
        except OSError: pass


@dp.callback_query(F.data == "adm:keys")
async def adm_keys(q: CallbackQuery):
    if not await admin_only(q): return
    c = conn(); rows = c.execute("SELECT key,owner_id,active FROM access_keys ORDER BY created_at DESC").fetchall(); c.close()
    lines = ["🔑 <b>KEY MANAGEMENT</b>", ""]
    for r in rows:
        lines.append(f"<code>{html.escape(r['key'])}</code>  |  {'🟢 ACTIVE' if r['active'] else '🔴 DISABLED'}  |  ID: {r['owner_id'] or 'UNUSED'}")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ GENERATE KEY", callback_data="adm:add_key")],
        [InlineKeyboardButton(text="⛔ DISABLE / ENABLE", callback_data="adm:key_toggle")],
        [InlineKeyboardButton(text="🔓 RESET KEY OWNER", callback_data="adm:key_reset")],
        [InlineKeyboardButton(text="⬅️ ADMIN PANEL", callback_data="adm:home")],
    ])
    await q.message.edit_text("\n".join(lines) if rows else "🔑 No keys.", reply_markup=kb); await q.answer()


@dp.callback_query(F.data == "adm:add_key")
async def adm_add_key(q: CallbackQuery, state: FSMContext):
    if not await admin_only(q): return
    await state.set_state(Form.admin_add_key)
    await q.message.answer("🔑 <b>Generate a new access key</b>\n\nSend a custom key, or send <code>RANDOM</code>.")
    await q.answer()


@dp.message(Form.admin_add_key)
async def adm_add_key_receive(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    raw = (m.text or "").strip()
    if raw.upper() == "RANDOM":
        key = "TIGER-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(10))
    else: key = raw
    if len(key) < 3 or "|" in key:
        await m.answer("❌ Invalid key."); return
    c = conn()
    try:
        c.execute("INSERT INTO access_keys(key,owner_id,active,created_at) VALUES(?,?,1,?)", (key, None, datetime.now().isoformat()))
        c.commit()
    except sqlite3.IntegrityError:
        c.close(); await m.answer("⚠️ This key already exists."); return
    c.close(); await state.clear()
    await m.answer(f"✅ <b>KEY CREATED</b>\n\n<code>{html.escape(key)}</code>")


@dp.callback_query(F.data.in_({"adm:key_toggle", "adm:key_reset"}))
async def adm_key_action(q: CallbackQuery, state: FSMContext):
    if not await admin_only(q): return
    action = "toggle" if q.data.endswith("toggle") else "reset"
    await state.update_data(key_action=action); await state.set_state(Form.admin_key_action)
    await q.message.answer("Send the exact key:")
    await q.answer()


@dp.message(Form.admin_key_action)
async def adm_key_action_receive(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    data = await state.get_data(); action = data.get("key_action"); key = (m.text or "").strip()
    c = conn(); row = c.execute("SELECT active FROM access_keys WHERE key=?", (key,)).fetchone()
    if not row:
        c.close(); await state.clear(); await m.answer("❌ Key not found."); return
    if action == "toggle": c.execute("UPDATE access_keys SET active=? WHERE key=?", (0 if row["active"] else 1, key)); msg = "enabled" if not row["active"] else "disabled"
    else: c.execute("UPDATE access_keys SET owner_id=NULL WHERE key=?", (key,)); msg = "owner reset"
    c.commit(); c.close(); await state.clear(); await m.answer(f"✅ Key {msg}.")


@dp.callback_query(F.data == "adm:countries")
async def adm_countries(q: CallbackQuery):
    if not await admin_only(q): return
    c = conn(); rows = c.execute("SELECT code,name,prefix,flag,active FROM countries ORDER BY name").fetchall(); c.close()
    text = "🌍 <b>COUNTRY MANAGEMENT</b>\n\n" + "\n".join(f"<code>{r['code']}</code> — {r['flag']} {r['name']} — {r['prefix']} — {'🟢' if r['active'] else '🔴'}" for r in rows)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ ADD COUNTRY", callback_data="adm:add_country")],
        [InlineKeyboardButton(text="✏️ EDIT COUNTRY", callback_data="adm:edit_country")],
        [InlineKeyboardButton(text="🟢 ENABLE / DISABLE", callback_data="adm:toggle_country")],
        [InlineKeyboardButton(text="🗑 REMOVE COUNTRY", callback_data="adm:remove_country")],
        [InlineKeyboardButton(text="⬅️ ADMIN PANEL", callback_data="adm:home")],
    ])
    await q.message.edit_text(text, reply_markup=kb); await q.answer()


@dp.callback_query(F.data == "adm:add_country")
async def adm_add_country(q: CallbackQuery, state: FSMContext):
    if not await admin_only(q): return
    await state.set_state(Form.admin_add_country); await q.message.answer("➕ Send: <code>IQ|Iraq|+964|🇮🇶</code>"); await q.answer()


@dp.message(Form.admin_add_country)
async def adm_add_country_receive(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    parts = [x.strip() for x in (m.text or "").split("|")]
    if len(parts) != 4: await m.answer("❌ Format: CODE|Country Name|Prefix|Flag"); return
    code,name,prefix,flag = parts
    c=conn(); c.execute("INSERT OR REPLACE INTO countries(code,name,prefix,flag,active) VALUES(?,?,?,?,1)",(code.upper(),name,prefix,flag)); c.commit(); c.close(); await state.clear(); await m.answer(f"✅ Country added: {flag} {name} ({prefix})")


@dp.callback_query(F.data == "adm:edit_country")
async def adm_edit_country(q: CallbackQuery, state: FSMContext):
    if not await admin_only(q): return
    await state.set_state(Form.admin_edit_country); await q.message.answer("✏️ Send: <code>IQ|Iraq|+964|🇮🇶</code>"); await q.answer()


@dp.message(Form.admin_edit_country)
async def adm_edit_country_receive(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    parts=[x.strip() for x in (m.text or "").split("|")]
    if len(parts)!=4: await m.answer("❌ Format: CODE|New Name|New Prefix|Flag"); return
    code,name,prefix,flag=parts; c=conn(); c.execute("UPDATE countries SET name=?,prefix=?,flag=? WHERE code=?",(name,prefix,flag,code.upper())); changed=c.total_changes; c.commit(); c.close(); await state.clear(); await m.answer("✅ Country updated." if changed else "❌ Country not found.")


@dp.callback_query(F.data == "adm:remove_country")
async def adm_remove_country(q: CallbackQuery, state: FSMContext):
    if not await admin_only(q): return
    await state.set_state(Form.admin_remove_country); await q.message.answer("🗑 Send the country CODE:"); await q.answer()


@dp.message(Form.admin_remove_country)
async def adm_remove_country_receive(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    code=(m.text or "").strip().upper(); c=conn(); c.execute("DELETE FROM countries WHERE code=?",(code,)); changed=c.total_changes; c.commit(); c.close(); await state.clear(); await m.answer("✅ Country removed." if changed else "❌ Country not found.")


@dp.callback_query(F.data == "adm:toggle_country")
async def adm_toggle_country(q: CallbackQuery, state: FSMContext):
    if not await admin_only(q): return
    await state.set_state(Form.admin_toggle_country)
    await q.message.answer("🟢 Send the country CODE to toggle, e.g. <code>IN</code>")
    await q.answer()


@dp.message(Form.admin_toggle_country)
async def adm_toggle_country_receive(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    code=(m.text or "").strip().upper(); c=conn(); row=c.execute("SELECT active FROM countries WHERE code=?",(code,)).fetchone()
    if not row:
        c.close(); await state.clear(); await m.answer("❌ Country not found."); return
    new=0 if row["active"] else 1; c.execute("UPDATE countries SET active=? WHERE code=?",(new,code)); c.commit(); c.close(); await state.clear(); await m.answer("✅ Country enabled." if new else "⛔ Country disabled.")


@dp.callback_query(F.data == "adm:services")
async def adm_services(q: CallbackQuery):
    if not await admin_only(q): return
    c=conn(); rows=c.execute("SELECT code,name,emoji,active,custom_emoji_id FROM services ORDER BY name").fetchall(); c.close()
    text="📱 <b>SERVICE MANAGEMENT</b>\n\n"+"\n".join(f"<code>{r['code']}</code> — {r['emoji']} {r['name']} — {'🟢' if r['active'] else '🔴'}" + (' — 🎨 ID SET' if r['custom_emoji_id'] else '') for r in rows)
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ ADD SERVICE",callback_data="adm:add_service")],
        [InlineKeyboardButton(text="✏️ EDIT SERVICE",callback_data="adm:edit_service")],
        [InlineKeyboardButton(text="🟢 ENABLE / DISABLE",callback_data="adm:toggle_service")],
        [InlineKeyboardButton(text="🗑 REMOVE SERVICE",callback_data="adm:remove_service")],
        [InlineKeyboardButton(text="⬅️ ADMIN PANEL",callback_data="adm:home")]])
    await q.message.edit_text(text,reply_markup=kb); await q.answer()


@dp.callback_query(F.data == "adm:add_service")
async def adm_add_service(q: CallbackQuery,state:FSMContext):
    if not await admin_only(q): return
    await state.set_state(Form.admin_add_service); await q.message.answer("➕ Send: <code>code|Name|Emoji</code>"); await q.answer()


@dp.message(Form.admin_add_service)
async def adm_add_service_receive(m:Message,state:FSMContext):
    if not is_admin(m.from_user.id): return
    parts=[x.strip() for x in (m.text or "").split("|")]
    if len(parts)!=3: await m.answer("❌ Format: code|Name|Emoji"); return
    code,name,emoji=parts; c=conn(); c.execute("INSERT OR REPLACE INTO services(code,name,emoji,active) VALUES(?,?,?,1)",(code.lower(),name,emoji)); c.commit(); c.close(); await state.clear(); await m.answer(f"✅ Service added: {emoji} {name}")


@dp.callback_query(F.data == "adm:edit_service")
async def adm_edit_service(q:CallbackQuery,state:FSMContext):
    if not await admin_only(q): return
    await state.set_state(Form.admin_edit_service); await q.message.answer("✏️ Send: <code>code|New Name|New Emoji</code>"); await q.answer()


@dp.message(Form.admin_edit_service)
async def adm_edit_service_receive(m:Message,state:FSMContext):
    if not is_admin(m.from_user.id): return
    parts=[x.strip() for x in (m.text or "").split("|")]
    if len(parts)!=3: await m.answer("❌ Format: code|New Name|New Emoji"); return
    code,name,emoji=parts; c=conn(); c.execute("UPDATE services SET name=?,emoji=? WHERE code=?",(name,emoji,code.lower())); changed=c.total_changes; c.commit(); c.close(); await state.clear(); await m.answer("✅ Service updated." if changed else "❌ Service not found.")


@dp.callback_query(F.data == "adm:remove_service")
async def adm_remove_service(q:CallbackQuery,state:FSMContext):
    if not await admin_only(q): return
    await state.set_state(Form.admin_remove_service); await q.message.answer("🗑 Send the service code:"); await q.answer()


@dp.message(Form.admin_remove_service)
async def adm_remove_service_receive(m:Message,state:FSMContext):
    if not is_admin(m.from_user.id): return
    code=(m.text or "").strip().lower(); c=conn(); c.execute("DELETE FROM services WHERE code=?",(code,)); changed=c.total_changes; c.commit(); c.close(); await state.clear(); await m.answer("✅ Service removed." if changed else "❌ Service not found.")


@dp.callback_query(F.data == "adm:toggle_service")
async def adm_toggle_service(q: CallbackQuery, state: FSMContext):
    if not await admin_only(q): return
    await state.set_state(Form.admin_toggle_service)
    await q.message.answer("🟢 Send the service code to toggle, e.g. <code>whatsapp</code>")
    await q.answer()


@dp.message(Form.admin_toggle_service)
async def adm_toggle_service_receive(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    code=(m.text or "").strip().lower(); c=conn(); row=c.execute("SELECT active FROM services WHERE code=?",(code,)).fetchone()
    if not row:
        c.close(); await state.clear(); await m.answer("❌ Service not found."); return
    new=0 if row["active"] else 1; c.execute("UPDATE services SET active=? WHERE code=?",(new,code)); c.commit(); c.close(); await state.clear(); await m.answer("✅ Service enabled." if new else "⛔ Service disabled.")


@dp.callback_query(F.data == "adm:ui")
async def adm_ui(q:CallbackQuery):
    if not await admin_only(q): return
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 লেখা পরিবর্তন",callback_data="adm:ui_text")],
        [InlineKeyboardButton(text="😀 ইমোজি পরিবর্তন",callback_data="adm:ui_emoji")],
        [InlineKeyboardButton(text="⬅️ ADMIN PANEL",callback_data="adm:home")]])
    await q.message.edit_text("🎨 <b>DRAGON SMS — UI SETTINGS</b>\n\nএখানে বটের লেখা অথবা ইমোজি পরিবর্তন করতে পারবে।",reply_markup=kb); await q.answer()



TEXT_TARGETS = {
    "title": "Welcome message",
    "get_number": "GET NUMBER button",
    "countries": "AVAILABLE COUNTRY button",
    "admin_button": "ADMIN PANEL button",
    "select_service": "SELECT SERVICE header",
    "select_country": "Select Your Country header",
    "traffic_title": "📊 Live Traffic Update heading",
    "traffic_BD": "Bangladesh traffic level (High/Medium/Low)",
    "traffic_IN": "India traffic level (High/Medium/Low)",
    "traffic_PK": "Pakistan traffic level (High/Medium/Low)",
    "number_header": "Number screen intro",
    "waiting_text": "Waiting for OTP text",
    "otp_title": "New DID Received title",
}

@dp.callback_query(F.data == "adm:ui_text")
async def ui_text_start(q: CallbackQuery, state: FSMContext):
    if not await admin_only(q): return
    buttons = [[InlineKeyboardButton(text=label, callback_data=f"adm:texttarget:{key}")] for key, label in TEXT_TARGETS.items()]
    buttons.append([InlineKeyboardButton(text="⬅️ UI SETTINGS", callback_data="adm:ui")])
    await q.message.edit_text("📝 <b>কোন লেখাটি পরিবর্তন করতে চাও?</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await q.answer()

@dp.callback_query(F.data.startswith("adm:texttarget:"))
async def ui_text_target(q: CallbackQuery, state: FSMContext):
    if not await admin_only(q): return
    key = q.data.split(":", 2)[2]
    if key not in TEXT_TARGETS: await q.answer("Not found", show_alert=True); return
    await state.update_data(ui_text_key=key)
    await state.set_state(Form.admin_text_replace)
    current = get_setting(key, "")
    await q.message.answer(f"📝 <b>{html.escape(TEXT_TARGETS[key])}</b>\nবর্তমান লেখা: <code>{html.escape(current)}</code>\n\nনতুন যে লেখাটি রাখতে চাও সেটি পাঠাও:")
    await q.answer()

@dp.callback_query(F.data == "adm:ui_emoji")
async def ui_emoji_start(q: CallbackQuery, state: FSMContext):
    if not await admin_only(q): return
    targets = [("logo_emoji", "Bot message logo"), ("select_service", "Service header"), ("select_country", "Country header"), ("number_header", "Number intro"), ("waiting_text", "Waiting text"), ("otp_title", "OTP title")]
    buttons = [[InlineKeyboardButton(text=label, callback_data=f"adm:emojitarget:{key}")] for key,label in targets]
    c=conn()
    for r in c.execute("SELECT code,name FROM services WHERE active=1 ORDER BY name").fetchall():
        buttons.append([InlineKeyboardButton(text=f"Service: {r['name']}", callback_data=f"adm:emojitarget:service:{r['code']}")])
    for r in c.execute("SELECT code,name FROM countries WHERE active=1 ORDER BY name").fetchall():
        buttons.append([InlineKeyboardButton(text=f"Country: {r['name']}", callback_data=f"adm:emojitarget:country:{r['code']}")])
    c.close()
    buttons.append([InlineKeyboardButton(text="⬅️ UI SETTINGS", callback_data="adm:ui")])
    await q.message.edit_text("😀 <b>কোন অংশের ইমোজি বদলাবে?</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await q.answer()

@dp.callback_query(F.data.startswith("adm:emojitarget:"))
async def ui_emoji_target(q: CallbackQuery, state: FSMContext):
    if not await admin_only(q): return
    target=q.data.split(":",2)[2]
    await state.update_data(ui_emoji_target=target)
    await state.set_state(Form.admin_emoji_replace)
    await q.message.answer("😀 এখন নতুন একটি ইমোজি পাঠাও। সেটি নির্বাচিত অংশে সেট হবে।")
    await q.answer()

BASE_ICON_CATALOG = {
    "select_service": ("SELECT SERVICE header", "icon_select_service"),
    "select_country": ("Select a country header", "icon_select_country"),
    "back": ("BACK button", "icon_back"),
    "access_key": ("Access key prompt", "icon_access_key"),
    "warning": ("Warning message", "icon_warning"),
    "wrong_key": ("Wrong key message", "icon_wrong_key"),
    "number_header": ("Number screen header", "icon_number_header"),
    "service_label": ("Service label", "icon_service_label"),
    "country_label": ("Country label", "icon_country_label"),
    "number_label": ("Number label", "icon_number_label"),
    "otp_title": ("New DID title", "icon_otp_title"),
}

def icon_catalog():
    catalog = dict(BASE_ICON_CATALOG)
    c=conn()
    for r in c.execute("SELECT code,name FROM services ORDER BY name").fetchall():
        catalog[f"service_{r['code']}"]=(f"{r['name']} button/icon", f"service:{r['code']}")
    for r in c.execute("SELECT code,name FROM countries ORDER BY name").fetchall():
        catalog[f"country_{r['code'].lower()}"]=(f"{r['name']} button/icon", f"country:{r['code']}")
    c.close()
    return catalog


def icon_current_id(target: str) -> str:
    if target.startswith("service:"):
        code=target.split(":",1)[1]; c=conn(); row=c.execute("SELECT custom_emoji_id FROM services WHERE code=?",(code,)).fetchone(); c.close(); return (row["custom_emoji_id"] if row else "") or ""
    if target.startswith("country:"):
        code=target.split(":",1)[1]; c=conn(); row=c.execute("SELECT custom_emoji_id FROM countries WHERE code=?",(code,)).fetchone(); c.close(); return (row["custom_emoji_id"] if row else "") or ""
    return get_setting(target, "")


def icon_target_label(target: str) -> str:
    for key,(label,real) in ICON_CATALOG.items():
        if real==target: return label
    return target


@dp.callback_query(F.data == "adm:icon_manager")
async def adm_icon_manager(q: CallbackQuery, state: FSMContext):
    if not await admin_only(q): return
    await state.set_state(Form.admin_icon_search)
    await q.message.answer("🔎 <b>ICON EMOJI MANAGER</b>\n\nType what you want to change, for example: <code>whatsapp</code>, <code>country</code>, <code>number</code>, <code>wrong</code>, <code>back</code>.")
    await q.answer()


@dp.message(Form.admin_icon_search)
async def adm_icon_search_receive(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    query=(m.text or "").strip().lower()
    matches=[]
    for key,(label,target) in icon_catalog().items():
        if query in key.lower() or query in label.lower(): matches.append((key,label,target))
    if not matches:
        await m.answer("❌ No icon found. Try: whatsapp, facebook, instagram, telegram, india, pakistan, bangladesh, service, country, number, key, warning, wrong, back.")
        return
    buttons=[]
    for key,label,target in matches:
        eid=icon_current_id(target)
        buttons.append([InlineKeyboardButton(text=f"{label} — {'SET' if eid else 'NOT SET'}", callback_data=f"adm:iconpick:{key}", icon_custom_emoji_id=eid or None)])
    buttons.append([InlineKeyboardButton(text="⬅️ ADMIN PANEL", callback_data="adm:home")])
    await state.clear()
    await m.answer("🔎 <b>SELECT AN ICON TO CHANGE</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@dp.callback_query(F.data.startswith("adm:iconpick:"))
async def adm_icon_pick(q: CallbackQuery, state: FSMContext):
    if not await admin_only(q): return
    key=q.data.split(":",2)[2]
    item=icon_catalog().get(key)
    if not item:
        await q.answer("Icon not found", show_alert=True); return
    _,target=item
    await state.update_data(icon_target=target, icon_key=key)
    await state.set_state(Form.admin_set_icon)
    current=icon_current_id(target)
    await q.message.answer(f"🎨 <b>{html.escape(icon_target_label(target))}</b>\n\nCurrent ID: <code>{html.escape(current or 'NOT SET')}</code>\n\nSend the new Telegram custom emoji ID.\nSend <code>off</code> to remove it.")
    await q.answer()


@dp.message(Form.admin_set_icon)
async def adm_set_icon_receive(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    value=(m.text or "").strip()
    data=await state.get_data(); target=data.get("icon_target")
    if not target:
        await state.clear(); await m.answer("❌ Icon target expired.", reply_markup=back_admin()); return
    eid="" if value.lower()=="off" else value
    if eid and not eid.isdigit():
        await m.answer("❌ Custom emoji ID must be numeric, or send <code>off</code>.")
        return
    if target.startswith("service:"):
        code=target.split(":",1)[1]; c=conn(); c.execute("UPDATE services SET custom_emoji_id=? WHERE code=?",(eid,code)); changed=c.total_changes; c.commit(); c.close()
    elif target.startswith("country:"):
        code=target.split(":",1)[1]; c=conn(); c.execute("UPDATE countries SET custom_emoji_id=? WHERE code=?",(eid,code)); changed=c.total_changes; c.commit(); c.close()
    else:
        set_setting(target,eid); changed=1
    await state.clear()
    await m.answer("✅ <b>Icon updated successfully.</b>" if changed else "❌ Target not found.", reply_markup=back_admin())


@dp.callback_query(F.data == "adm:text")
async def adm_text(q:CallbackQuery,state:FSMContext):
    if not await admin_only(q): return
    await state.set_state(Form.admin_edit_text)
    await q.message.answer("📝 Send one setting per message:\n<code>title|New welcome</code>\n<code>get_number|📞 GET NUMBER</code>\n<code>countries|🌍 AVAILABLE COUNTRY</code>\n<code>select_service|SELECT SERVICE</code>\n<code>number_header|📲 ...</code>\n<code>waiting_text|⏳ WAITING...</code>\n<code>otp_title|✉️ New DID Received!</code>"); await q.answer()


@dp.message(Form.admin_edit_text)
async def adm_text_receive(m:Message,state:FSMContext):
    if not is_admin(m.from_user.id): return
    parts=(m.text or "").split("|",1)
    allowed={"title","get_number","countries","admin_button","select_service","number_header","waiting_text","otp_title"}
    if len(parts)!=2 or parts[0] not in allowed: await m.answer("❌ Invalid setting format."); return
    set_setting(parts[0],parts[1]); await state.clear(); await m.answer("✅ Updated.")


@dp.callback_query(F.data == "adm:logo")
async def adm_logo(q:CallbackQuery,state:FSMContext):
    if not await admin_only(q): return
    await state.set_state(Form.admin_set_logo); await q.message.answer("🐉 Send the emoji you want to use as the logo in bot messages (for example 🐉, 🐲, ⚡). Send <code>off</code> to reset to 🐉."); await q.answer()


@dp.message(Form.admin_set_logo)
async def adm_logo_receive(m:Message,state:FSMContext):
    if not is_admin(m.from_user.id): return
    value=(m.text or "").strip()
    if not value:
        await m.answer("❌ Please send one emoji, or send <code>off</code>."); return
    set_setting("logo_emoji", "🐉" if value.lower()=="off" else value)
    await state.clear(); await m.answer("✅ Message logo emoji updated: " + html.escape(get_setting("logo_emoji", "🐉")), reply_markup=back_admin())


@dp.callback_query(F.data == "adm:service_emoji")
async def adm_service_emoji(q: CallbackQuery, state: FSMContext):
    if not await admin_only(q): return
    await state.set_state(Form.admin_set_service_emoji)
    await q.message.answer("🎨 Send: <code>service_code|custom_emoji_id</code>\nExample: <code>whatsapp|5368324170001234567</code>\nSend <code>off</code> to restore the normal emoji.")
    await q.answer()


@dp.message(Form.admin_set_service_emoji)
async def adm_service_emoji_receive(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    parts=(m.text or "").split("|",1)
    if len(parts)!=2:
        await m.answer("❌ Format: service_code|custom_emoji_id")
        return
    code, eid=parts[0].strip().lower(), parts[1].strip()
    c=conn(); row=c.execute("SELECT code FROM services WHERE code=?",(code,)).fetchone()
    if not row:
        c.close(); await state.clear(); await m.answer("❌ Service not found."); return
    if eid.lower()=="off": eid=""
    c.execute("UPDATE services SET custom_emoji_id=? WHERE code=?",(eid,code)); c.commit(); c.close()
    await state.clear(); await m.answer("✅ Service custom emoji ID updated.")


@dp.callback_query(F.data == "adm:channel")
async def adm_channel(q:CallbackQuery,state:FSMContext):
    if not await admin_only(q): return
    await state.set_state(Form.admin_set_channel); await q.message.answer("📢 Send @channelusername or public t.me link."); await q.answer()


@dp.message(Form.admin_set_channel)
async def adm_channel_receive(m:Message,state:FSMContext):
    if not is_admin(m.from_user.id): return
    value=(m.text or "").strip()
    if value.startswith("https://t.me/"): value="@"+value.rstrip("/").split("/")[-1]
    if not value.startswith("@"): await m.answer("❌ Use @channelusername or a public t.me link."); return
    set_setting("channel",value); await state.clear(); await m.answer(f"✅ Required channel changed to {html.escape(value)}")


@dp.callback_query(F.data == "adm:support")
async def adm_support(q:CallbackQuery,state:FSMContext):
    if not await admin_only(q): return
    await state.set_state(Form.admin_set_support); await q.message.answer("🆘 Send support username, e.g. <code>@TIGERFLOW07</code>"); await q.answer()


@dp.message(Form.admin_set_support)
async def adm_support_receive(m:Message,state:FSMContext):
    if not is_admin(m.from_user.id): return
    value=(m.text or "").strip()
    if not value.startswith("@"): await m.answer("❌ Use @username."); return
    set_setting("support",value); await state.clear(); await m.answer("✅ Support updated.")


@dp.callback_query(F.data == "adm:users")
async def adm_users(q: CallbackQuery):
    if not await admin_only(q):
        return
    await render_user_list(q, 0)


async def render_user_list(q: CallbackQuery, page: int = 0):
    per_page = 8
    c = conn()
    total = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
    rows = c.execute(
        "SELECT user_id,username,verified,joined,created_at FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (per_page, page * per_page),
    ).fetchall()
    c.close()

    lines = ["👥 <b>USER DETAILS</b>", "", f"Total users: <b>{total}</b>", ""]
    buttons = []
    for r in rows:
        uname = f"@{html.escape(r['username'])}" if r["username"] else "No username"
        status = "✅" if r["verified"] else "⏳"
        lines.append(f"{status} <code>{r['user_id']}</code> — {uname}")
        buttons.append([InlineKeyboardButton(text=f"👤 {r['user_id']}", callback_data=f"adm:user:{r['user_id']}")])
    if not rows:
        lines.append("No users yet.")

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ PREV", callback_data=f"adm:users:{page-1}"))
    if (page + 1) * per_page < total:
        nav.append(InlineKeyboardButton(text="NEXT ➡️", callback_data=f"adm:users:{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="⬅️ ADMIN PANEL", callback_data="adm:home")])
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    if isinstance(q, CallbackQuery):
        await q.message.edit_text("\n".join(lines), reply_markup=markup)
        await q.answer()


@dp.callback_query(F.data.regexp(r"^adm:users:\d+$"))
async def adm_users_page(q: CallbackQuery):
    if not await admin_only(q):
        return
    page = int(q.data.rsplit(":", 1)[1])
    await render_user_list(q, page)


@dp.callback_query(F.data.startswith("adm:user:"))
async def adm_user_detail(q: CallbackQuery):
    if not await admin_only(q):
        return
    uid = int(q.data.rsplit(":", 1)[1])
    c = conn()
    u = c.execute("SELECT user_id,username,verified FROM users WHERE user_id=?", (uid,)).fetchone()
    c.close()
    if not u:
        await q.answer("User not found.", show_alert=True)
        return
    uname = f"@{html.escape(u['username'])}" if u["username"] else "No username"
    key_status = "VERIFIED" if u["verified"] else "NOT VERIFIED"
    lines = [
        "👤 <b>USER DETAILS</b>",
        "",
        f"🆔 Telegram ID: <code>{u['user_id']}</code>",
        f"👤 Username: {uname}",
        f"🔑 Key status: <b>{key_status}</b>",
    ]
    await q.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ USERS", callback_data="adm:users")],
        [InlineKeyboardButton(text="⬅️ ADMIN PANEL", callback_data="adm:home")],
    ]))
    await q.answer()


@dp.callback_query(F.data == "adm:stats")
async def adm_stats(q:CallbackQuery):
    if not await admin_only(q): return
    c=conn(); users=c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]; verified=c.execute("SELECT COUNT(*) n FROM users WHERE verified=1").fetchone()["n"]; keys=c.execute("SELECT COUNT(*) n FROM access_keys").fetchone()["n"]; countries=c.execute("SELECT COUNT(*) n FROM countries WHERE active=1").fetchone()["n"]; services=c.execute("SELECT COUNT(*) n FROM services WHERE active=1").fetchone()["n"]; pending_buy=c.execute("SELECT COUNT(*) n FROM key_buy_requests WHERE status='pending'").fetchone()["n"]; approved_buy=c.execute("SELECT COUNT(*) n FROM key_buy_requests WHERE status='approved'").fetchone()["n"]; c.close()
    await q.message.edit_text(f"📊 <b>BOT STATISTICS</b>\n\n👤 Users: {users}\n✅ Verified IDs: {verified}\n🔑 Keys: {keys}\n🛒 Pending KEY BUY: {pending_buy}\n💳 Approved KEY BUY: {approved_buy}\n🌍 Active Countries: {countries}\n📱 Active Services: {services}",reply_markup=back_admin()); await q.answer()


@dp.errors()
async def errors_handler(event: ErrorEvent):
    log.exception("Unhandled update error: %r", event.exception)


async def main():
    init_db()
    me=await bot.get_me(); log.info("Bot connected: @%s", me.username)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
