"""
Telegram Group Admin Bot + AI Chatbot
--------------------------------------
A full-featured Telegram group management bot: moderation, custom filters,
a word blocklist, welcome messages, an AI chatbot (Google Gemini), and a
multiplayer word chain game.

Admin Commands: /kick /ban /unban /mute /unmute /warn /warnings /resetwarns
                /promote /demote /pin /unpin /purge /rules /setrules
                /filters /delfilters /filterlist /addblacklist /removeblock
                /blocklist /approve /unapprove
                /setwelcome /delsetwelcome /endgame
Fun Commands:   /truth /dare /tr <language> /google <question> /game /join
Other:          /start /commands /info /admins /developer /ping
Owner-only:     /broadcast <message> — reply to a message/sticker, or pass
                text directly. Sends an announcement to every chat the bot
                has seen. /stats — shows how many groups the bot is active
                in and how many users have DM'd it. Both are restricted to
                the DEVELOPER_CHAT_ID user and intentionally left out of
                the public /commands menu.
Passive:        AI chat (@mention or reply in groups, always in DM) +
                sticker echo (DM always, groups only when replying to the bot)

Most moderation commands accept EITHER:
  - a reply to the target user's message, e.g. reply + "/ban"
  - OR "@username" as the first argument, e.g. "/ban @someuser spamming"
  - OR their numeric user ID, e.g. "/ban 123456789 spamming" (useful when
    the user has no public @username)
  (Telegram can only resolve @username/ID for users who have previously
  interacted with this bot/group — reply-based targeting always works.)

/filters, /addblacklist, and /setwelcome each accept EITHER:
  - a reply to a text message or sticker, e.g. reply-to-sticker + "/filters eren"
  - OR inline text, e.g. "/filters eren Hello there!"
  /addblacklist also accepts a trailing "{warn}" flag to auto-warn whoever
  triggers that entry (e.g. "/addblacklist badword {warn}").

Setup:
1. pip install -r requirements.txt
2. Set TELEGRAM_BOT_TOKEN env var (bot token from BotFather)
3. Set GEMINI_API_KEY env var (free, from aistudio.google.com/apikey)
4. Optionally set DEVELOPER_CHAT_ID env var (your numeric Telegram user ID)
   to get notified whenever someone starts the bot in a private chat.
5. Add the bot to your group and make it an ADMIN with these rights:
   Ban users, Delete messages, Pin messages, Add new admins (for /promote)
6. Run: python group_admin_bot.py

Notes:
- All bot state (warnings, filters, blocklist, rules, welcome message, game
  state) is kept in memory and resets when the process restarts.
- The word chain game validates real English words via the free
  dictionaryapi.dev API (fails open if that service is unreachable).
"""

import html
import logging
import httpx
import os
import random
import re
import time
import traceback
from datetime import timedelta

from google import genai
from google.genai import types as genai_types
from telegram import Update, ChatPermissions, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatMemberStatus, ChatType
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "PUT_YOUR_BOT_TOKEN_HERE")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
DEVELOPER_CHAT_ID = os.environ.get("DEVELOPER_CHAT_ID", "")  # your numeric Telegram user ID
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
GEMINI_MODEL = "gemini-3.6-flash"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# IN-MEMORY DATA STORES (reset on restart — see README for persistence notes)
# ---------------------------------------------------------------------------
CHAT_HISTORY: dict[int, list] = {}          # chat_id -> [{"role","parts"}, ...]
MAX_HISTORY_MESSAGES = 10

WARNINGS: dict[int, dict[int, int]] = {}    # chat_id -> {user_id: count}
MAX_WARNINGS = 3

RULES: dict[int, str] = {}                  # chat_id -> rules text
FILTERS: dict[int, dict[str, dict]] = {}    # chat_id -> {trigger: {"type": "text"/"sticker", "content"/"file_id": ...}}
BLOCKLIST: dict[int, dict[str, bool]] = {}  # chat_id -> {blocked_word: warn_flag}
STICKER_BLOCKLIST: dict[int, dict[str, bool]] = {}  # chat_id -> {sticker_unique_id: warn_flag}
WELCOME: dict[int, dict] = {}               # chat_id -> {"type": "text"/"sticker", "content"/"file_id": ...}
APPROVED_USERS: dict[int, set] = {}         # chat_id -> {user_id, ...} exempt from the blocklist
KNOWN_CHATS: set = set()                    # every chat_id the bot has seen a message from
GAME_STATE: dict[int, dict] = {}            # chat_id -> word-chain game state

TRUTH_QUESTIONS = [
    "What's the most embarrassing thing that's ever happened to you?",
    "What's a secret you've never told anyone in this group?",
    "What's your biggest fear?",
    "Who was your first crush?",
    "What's the weirdest dream you've ever had?",
    "What's a lie you told that you never got caught for?",
    "What's the most childish thing you still do?",
    "What app do you spend the most time on and why?",
    "What's your most irrational fear?",
    "What's something you pretend to like but actually hate?",
    "What's the last thing you Googled?",
    "What's your worst habit?",
    "Have you ever cheated on a test?",
    "What's the most trouble you've ever been in?",
    "What's your most used emoji and why?",
    "What's a talent you're embarrassed to admit you have?",
    "What's the pettiest thing you've ever done?",
    "Who in this group would you trust with a secret?",
    "What's your guilty pleasure song?",
    "What's the weirdest food combination you actually enjoy?",
]

DARE_CHALLENGES = [
    "Send the last photo in your gallery (no cheating!).",
    "Text your crush 'hi' right now.",
    "Speak in an accent for the next 3 messages.",
    "Change your profile picture to something silly for 10 minutes.",
    "Send a voice note singing your favorite song.",
    "Type your next message using only emojis.",
    "Reply to this using only one hand... just kidding, tell everyone your last search history topic.",
    "Do 10 push-ups right now and tell us you did.",
    "Send a message in all caps confessing your favorite guilty pleasure show.",
    "Let the group pick your profile bio for the next hour.",
    "Message the last person you texted and say 'I miss you'.",
    "Post a throwback photo from 3+ years ago.",
    "Talk in rhymes for your next 3 messages.",
    "Tell the group your most-used autocorrect fail.",
    "Impersonate another group member for one message.",
    "Send a compliment to the person above you in chat.",
    "Reveal the last app you downloaded.",
    "Type out the alphabet backwards without checking.",
    "Send a fun fact nobody in the group knows about you.",
    "Set your status to something funny for 1 hour.",
]

# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
class TargetUser:
    """Minimal user-like wrapper so both reply-based and @username-based
    targets can be handled the same way."""
    def __init__(self, id, full_name, username=None):
        self.id = id
        self.full_name = full_name
        self.username = username


def esc(text) -> str:
    """Escapes text for safe embedding inside an HTML parse_mode message."""
    return html.escape(str(text), quote=False)


def mention(obj) -> str:
    """Returns a clickable @username mention if available, else a bold name.
    Accepts telegram.User, TargetUser, or a plain dict with id/full_name/username.
    Output is HTML-safe (use with parse_mode="HTML")."""
    if isinstance(obj, dict):
        username = obj.get("username")
        full_name = obj.get("full_name") or "Player"
    else:
        username = getattr(obj, "username", None)
        full_name = getattr(obj, "full_name", None) or "User"
    if username:
        return f"@{esc(username)}"
    return f"<b>{esc(full_name)}</b>"


async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(update.effective_chat.id, user_id)
        return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception as e:
        logger.warning(f"is_admin check failed: {e}")
        return False


async def require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not await is_admin(update, context, user.id):
        await update.message.reply_text("❌ Sorry, this command is reserved for group admins.")
        return False
    return True


def get_target_user(update: Update):
    if update.message.reply_to_message:
        return update.message.reply_to_message.from_user
    return None


async def get_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resolves the command's target user via reply, a leading @username
    argument, or a raw numeric user ID (checks all — falls back to reply if
    @username/ID resolution can't fetch full details).
    Returns (target_or_None, remaining_args)."""
    args = list(context.args) if context.args else []
    if args and args[0].startswith("@"):
        username = args[0][1:]
        try:
            chat = await context.bot.get_chat(f"@{username}")
            full_name = getattr(chat, "first_name", None) or username
            if getattr(chat, "last_name", None):
                full_name += f" {chat.last_name}"
            target = TargetUser(chat.id, full_name, getattr(chat, "username", username))
            return target, args[1:]
        except Exception as e:
            logger.warning(f"Couldn't resolve @{username}: {e}")
            reply_user = get_target_user(update)
            if reply_user:
                return reply_user, args[1:]
            return None, args[1:]

    if args and args[0].lstrip("-").isdigit():
        user_id = int(args[0])
        try:
            chat = await context.bot.get_chat(user_id)
            full_name = getattr(chat, "first_name", None) or f"User {user_id}"
            if getattr(chat, "last_name", None):
                full_name += f" {chat.last_name}"
            target = TargetUser(chat.id, full_name, getattr(chat, "username", None))
        except Exception as e:
            logger.warning(f"Couldn't fetch details for user id {user_id}, using the ID directly: {e}")
            # The action itself only needs the numeric ID, so this still works
            target = TargetUser(user_id, f"User {user_id}", None)
        return target, args[1:]

    reply_user = get_target_user(update)
    if reply_user:
        return reply_user, args
    return None, args


async def ensure_bot_is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    bot_member = await context.bot.get_chat_member(update.effective_chat.id, context.bot.id)
    if bot_member.status != ChatMemberStatus.ADMINISTRATOR:
        await update.message.reply_text(
            "⚠️ I'll need to be an admin first — please grant me admin rights (ban/mute/pin permissions), and I'll be ready to go."
        )
        return False
    return True


NO_TARGET_TEXT = (
    "🤔 I need to know who you mean — reply to their message, or add <b>@username</b> "
    "or their <b>numeric user ID</b> right after the command."
)


# ---------------------------------------------------------------------------
# BASIC COMMANDS
# ---------------------------------------------------------------------------
def build_commands_text() -> str:
    return (
        "<b>📋 All Commands</b>\n\n"
        "<b>🛡️ Moderation</b>\n"
        "👢 /kick — remove a user (can rejoin)\n"
        "🔨 /ban — permanently ban a user\n"
        "✅ /unban &lt;user id or @username&gt; — unban a user\n"
        "🔇 /mute [minutes] — mute a user\n"
        "🔊 /unmute — unmute a user\n"
        "⚠️ /warn [reason] — warn a user\n"
        "📋 /warnings — check a user's warnings\n"
        "♻️ /resetwarns — reset a user's warnings\n"
        "⬆️ /promote — make a user an admin\n"
        "⬇️ /demote — remove admin rights\n"
        "📌 /pin — pin the replied message\n"
        "📍 /unpin — remove the pinned message\n"
        "🧹 /purge — delete messages from reply point onward\n\n"
        "<b>⚙️ Group Setup</b>\n"
        "📜 /rules — view group rules\n"
        "📝 /setrules &lt;text&gt; — set group rules\n"
        "🧩 /filters &lt;trigger&gt; [response] — reply to a message/sticker (or type a response) to save an auto-reply\n"
        "🗑️ /delfilters &lt;trigger&gt; — remove an auto-reply\n"
        "📋 /filterlist — see how many filters are active\n"
        "🚫 /addblacklist &lt;words&gt; — reply to a message/sticker (or list words) to block it; add {warn} to also warn the sender\n"
        "♻️ /removeblock &lt;words&gt; — unblock words or a replied sticker\n"
        "📋 /blocklist — see everything on the blocklist\n"
        "✅ /approve — exempt a user from the blocklist (admins are always exempt)\n"
        "♻️ /unapprove — remove a user's blocklist exemption\n"
        "💬 /setwelcome [text] — reply to a message/sticker (or type text) as the welcome message — {name} or {username} mentions the new member\n"
        "🗑️ /delsetwelcome — remove the welcome message\n\n"
        "<b>🎉 Fun</b>\n"
        "🤔 /truth — random truth question\n"
        "🔥 /dare — random dare challenge\n"
        "🌐 /tr &lt;language&gt; — translate a replied message\n"
        "🔎 /google &lt;question&gt; — search Google for a live answer\n"
        "🔗 /game — open a word chain lobby (send again to start once 2+ joined)\n"
        "🙋 /join — join an open lobby\n\n"
        "<b>ℹ️ Other</b>\n"
        "👤 /info — view a user's info\n"
        "👑 /admins — list the group's admins and owner\n"
        "👨‍💻 /developer — meet the bot's developer\n"
        "🏓 /ping — check if the bot is online\n\n"
        "Targeting a user: <b>reply</b> to their message, or add <b>@username</b> or their "
        "<b>numeric ID</b> right after the command."
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_username = context.bot.username
    add_to_group_url = (
        f"https://t.me/{bot_username}?startgroup=true"
        "&admin=delete_messages+restrict_members+invite_users+pin_messages+promote_members"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add me to your Group", url=add_to_group_url)],
        [
            InlineKeyboardButton("📋 All Commands", callback_data="show_commands"),
            InlineKeyboardButton("👨‍💻 Developer", url="https://t.me/liesworlds"),
        ],
    ])
    await update.message.reply_text(
        "👋 <b>Hello! I'm your group's management assistant.</b>\n\n"
        "I handle moderation (kick/ban/mute/warn), group setup (filters, blocklist, "
        "welcome messages), and fun stuff (truth/dare, translation, a word chain game) — "
        "all in one bot.\n\n"
        "Tap <b>Add me to your Group</b> below to get started, then make me an <b>ADMIN</b> "
        "so moderation commands work properly.",
        parse_mode="HTML",
        reply_markup=keyboard,
    )

    # Notify the developer whenever someone starts the bot in a private chat
    if update.effective_chat.type == ChatType.PRIVATE and DEVELOPER_CHAT_ID:
        user = update.effective_user
        try:
            await context.bot.send_message(
                int(DEVELOPER_CHAT_ID),
                f"🆕 <b>New user started the bot!</b>\n\n"
                f"👤 Name: {esc(user.full_name)}\n"
                f"🔗 Username: {mention(user)}\n"
                f"🆔 ID: {user.id}",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"Couldn't notify developer of new /start: {e}")


async def commands_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(build_commands_text(), parse_mode="HTML")


async def show_commands_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(build_commands_text(), parse_mode="HTML")


async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target, _ = await get_target(update, context)
    target = target or update.effective_user
    admin_status = await is_admin(update, context, target.id)
    username_display = f"@{esc(target.username)}" if target.username else "N/A"
    text = (
        f"ℹ️ <b>User Info</b>\n\n"
        f"👤 Name: {esc(target.full_name)}\n"
        f"🆔 ID: {target.id}\n"
        f"🔗 Username: {username_display}\n"
        f"🛡️ Admin: {'Yes' if admin_status else 'No'}"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == ChatType.PRIVATE:
        await update.message.reply_text("⚠️ This command only works in groups.")
        return
    try:
        members = await context.bot.get_chat_administrators(update.effective_chat.id)
    except Exception as e:
        await update.message.reply_text(f"❌ Couldn't fetch admins: {friendly_error(e)}", parse_mode="HTML")
        return

    owner = [m for m in members if m.status == ChatMemberStatus.OWNER]
    admins = [m for m in members if m.status == ChatMemberStatus.ADMINISTRATOR]

    lines = ["👑 <b>Group Admins</b>\n"]
    for m in owner:
        lines.append(f"👑 {mention(m.user)} — Owner")
    for m in admins:
        lines.append(f"🛡️ {mention(m.user)}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def developer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👨‍💻 <b>About my developer</b>\n\n"
        "I was built and maintained by @liesworlds ✨\n"
        "For new features, feedback, or bug reports — feel free to reach out directly!",
        parse_mode="HTML",
    )


async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start = time.monotonic()
    msg = await update.message.reply_text("🏓 Pinging...")
    elapsed_ms = (time.monotonic() - start) * 1000
    await msg.edit_text(f"🏓 Pong! {elapsed_ms:.0f} ms — I'm up and running.")


# ---------------------------------------------------------------------------
# KICK / BAN / UNBAN
# ---------------------------------------------------------------------------
async def kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    target, _ = await get_target(update, context)
    if not target:
        await update.message.reply_text(NO_TARGET_TEXT, parse_mode="HTML")
        return
    chat_id = update.effective_chat.id
    try:
        await context.bot.ban_chat_member(chat_id, target.id)
        await context.bot.unban_chat_member(chat_id, target.id)
        await update.message.reply_text(f"👢 {mention(target)} has been removed from the group and is free to rejoin.", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Kick failed: {friendly_error(e)}", parse_mode="HTML")


async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    target, args = await get_target(update, context)
    if not target:
        await update.message.reply_text(NO_TARGET_TEXT, parse_mode="HTML")
        return
    reason = " ".join(args) if args else "No reason given"
    chat_id = update.effective_chat.id
    try:
        await context.bot.ban_chat_member(chat_id, target.id)
        await update.message.reply_text(f"🔨 {mention(target)} has been permanently banned.\n📝 Reason: {esc(reason)}", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Ban failed: {friendly_error(e)}", parse_mode="HTML")


async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return

    reply_user = get_target_user(update)
    if reply_user:
        user_id = reply_user.id
        label = mention(reply_user)
    elif context.args:
        identifier = context.args[0]
        try:
            if identifier.startswith("@"):
                chat = await context.bot.get_chat(identifier)
                user_id = chat.id
                label = f"@{esc(chat.username or identifier[1:])}"
            else:
                user_id = int(identifier)
                label = f"User {user_id}"
        except Exception as e:
            await update.message.reply_text(
                f"❌ Couldn't find that user ({e}). Telegram can only resolve @username if that "
                f"person has interacted with this bot/group before — try replying to one of their "
                f"old messages instead, or use their numeric user ID."
            )
            return
    else:
        await update.message.reply_text("🤔 Reply to the user, or use /unban <user_id> or /unban @username.")
        return

    try:
        await context.bot.unban_chat_member(update.effective_chat.id, user_id, only_if_banned=True)
        await update.message.reply_text(f"✅ {label} has been unbanned.", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Unban failed: {esc(str(e))}", parse_mode="HTML")


# ---------------------------------------------------------------------------
# MUTE / UNMUTE
# ---------------------------------------------------------------------------
async def mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    target, args = await get_target(update, context)
    if not target:
        await update.message.reply_text(NO_TARGET_TEXT, parse_mode="HTML")
        return

    minutes = None
    if args:
        try:
            minutes = int(args[0])
        except ValueError:
            await update.message.reply_text("🔢 That doesn't look like a number — try something like /mute 30.")
            return

    permissions = ChatPermissions(
        can_send_messages=False, can_send_audios=False, can_send_documents=False,
        can_send_photos=False, can_send_videos=False, can_send_video_notes=False,
        can_send_voice_notes=False, can_send_polls=False, can_send_other_messages=False,
        can_add_web_page_previews=False,
    )
    until_date = update.message.date + timedelta(minutes=minutes) if minutes else None

    try:
        await context.bot.restrict_chat_member(
            update.effective_chat.id, target.id, permissions=permissions, until_date=until_date
        )
        duration_text = f" for {minutes} minutes" if minutes else " (until unmuted)"
        await update.message.reply_text(f"🔇 {mention(target)} has been muted{duration_text}.", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Mute failed: {friendly_error(e)}", parse_mode="HTML")


async def unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    target, _ = await get_target(update, context)
    if not target:
        await update.message.reply_text(NO_TARGET_TEXT, parse_mode="HTML")
        return

    permissions = ChatPermissions(
        can_send_messages=True, can_send_audios=True, can_send_documents=True,
        can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
        can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
        can_add_web_page_previews=True,
    )
    try:
        await context.bot.restrict_chat_member(update.effective_chat.id, target.id, permissions=permissions)
        await update.message.reply_text(f"🔊 {mention(target)} may now send messages again.", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Unmute failed: {friendly_error(e)}", parse_mode="HTML")


# ---------------------------------------------------------------------------
# WARNINGS
# ---------------------------------------------------------------------------
async def apply_warning(context: ContextTypes.DEFAULT_TYPE, chat_id: int, target, reason: str) -> int:
    """Increments a user's warning count, auto-banning at MAX_WARNINGS.
    Returns the new warning count. Sends its own chat messages."""
    WARNINGS.setdefault(chat_id, {})
    WARNINGS[chat_id][target.id] = WARNINGS[chat_id].get(target.id, 0) + 1
    count = WARNINGS[chat_id][target.id]

    await context.bot.send_message(
        chat_id,
        f"⚠️ {mention(target)} has received a warning. ({count}/{MAX_WARNINGS})\n📝 Reason: {esc(reason)}",
        parse_mode="HTML",
    )

    if count >= MAX_WARNINGS:
        try:
            await context.bot.ban_chat_member(chat_id, target.id)
            WARNINGS[chat_id][target.id] = 0
            await context.bot.send_message(
                chat_id,
                f"🔨 {mention(target)} has reached {MAX_WARNINGS} warnings and has been removed from the group.",
                parse_mode="HTML",
            )
        except Exception as e:
            await context.bot.send_message(chat_id, f"❌ Auto-ban failed: {friendly_error(e)}", parse_mode="HTML")

    return count


async def warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    target, args = await get_target(update, context)
    if not target:
        await update.message.reply_text(NO_TARGET_TEXT, parse_mode="HTML")
        return

    reason = " ".join(args) if args else "No reason given"
    await apply_warning(context, update.effective_chat.id, target, reason)


async def warnings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target, _ = await get_target(update, context)
    target = target or update.effective_user
    chat_id = update.effective_chat.id
    count = WARNINGS.get(chat_id, {}).get(target.id, 0)
    await update.message.reply_text(f"📋 {mention(target)}'s warnings: {count}/{MAX_WARNINGS}", parse_mode="HTML")


async def resetwarns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    target, _ = await get_target(update, context)
    if not target:
        await update.message.reply_text(NO_TARGET_TEXT, parse_mode="HTML")
        return
    chat_id = update.effective_chat.id
    WARNINGS.setdefault(chat_id, {})[target.id] = 0
    await update.message.reply_text(f"♻️ {mention(target)}'s warnings have been reset.", parse_mode="HTML")


# ---------------------------------------------------------------------------
# PROMOTE / DEMOTE
# ---------------------------------------------------------------------------
def friendly_gemini_error(e: Exception) -> str:
    """Turns raw Gemini API errors (especially free-tier rate limits) into a
    friendly, plain-language message."""
    text = str(e)
    if "429" in text or "RESOURCE_EXHAUSTED" in text.upper() or "quota" in text.lower():
        return "⏳ I've hit my AI usage limit for now — please try again in a minute or two."
    return "😕 Something went wrong on my end — please try again shortly."


def friendly_error(e: Exception) -> str:
    """Turns common raw Telegram API errors into a helpful hint."""
    text = str(e)
    upper = text.upper()
    if "CHAT_ADMIN_REQUIRED" in upper or "NOT ENOUGH RIGHTS" in upper:
        return (
            f"{esc(text)}\n\n💡 Make sure I have the right admin permissions in this group "
            f"(Ban users / Delete messages / Pin messages / Add new admins as relevant)."
        )
    if "USER_ADMIN_INVALID" in upper or "CANT_REMOVE_CHAT_OWNER" in upper:
        return f"{esc(text)}\n\n💡 I can't act on another admin or the group owner — demote them first."
    if "USER_NOT_PARTICIPANT" in upper:
        return f"{esc(text)}\n\n💡 That user doesn't seem to be a member of this group."
    if "CHAT NOT FOUND" in upper:
        return (
            f"{esc(text)}\n\n💡 I can only resolve @username for people who've already interacted "
            f"with this bot/group. Try replying to one of their messages instead."
        )
    return esc(text)


async def promote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    target, _ = await get_target(update, context)
    if not target:
        await update.message.reply_text(NO_TARGET_TEXT, parse_mode="HTML")
        return
    try:
        await context.bot.promote_chat_member(
            update.effective_chat.id, target.id,
            can_change_info=True, can_delete_messages=True, can_invite_users=True,
            can_restrict_members=True, can_pin_messages=True, can_promote_members=False,
        )
        await update.message.reply_text(f"⬆️ {mention(target)} has been made an admin.", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Promote failed: {friendly_error(e)}", parse_mode="HTML")


async def demote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    target, _ = await get_target(update, context)
    if not target:
        await update.message.reply_text(NO_TARGET_TEXT, parse_mode="HTML")
        return
    try:
        await context.bot.promote_chat_member(
            update.effective_chat.id, target.id,
            can_change_info=False, can_delete_messages=False, can_invite_users=False,
            can_restrict_members=False, can_pin_messages=False, can_promote_members=False,
        )
        await update.message.reply_text(f"⬇️ {mention(target)}'s admin rights have been removed.", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Demote failed: {friendly_error(e)}", parse_mode="HTML")


# ---------------------------------------------------------------------------
# PIN / UNPIN / PURGE
# ---------------------------------------------------------------------------
async def pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("📌 Reply to the message you'd like me to pin with /pin.")
        return
    try:
        await context.bot.pin_chat_message(update.effective_chat.id, update.message.reply_to_message.message_id)
        await update.message.reply_text("📌 Done — that message is now pinned!")
    except Exception as e:
        await update.message.reply_text(f"❌ Pin failed: {esc(str(e))}", parse_mode="HTML")


async def unpin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    try:
        await context.bot.unpin_chat_message(update.effective_chat.id)
        await update.message.reply_text("📍 Got it — that message has been unpinned.")
    except Exception as e:
        await update.message.reply_text(f"❌ Unpin failed: {esc(str(e))}", parse_mode="HTML")


async def purge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("🧹 Reply to the message you'd like to start deleting from, then use /purge.")
        return

    chat_id = update.effective_chat.id
    start_id = update.message.reply_to_message.message_id
    end_id = update.message.message_id

    deleted = 0
    for msg_id in range(start_id, end_id + 1):
        try:
            await context.bot.delete_message(chat_id, msg_id)
            deleted += 1
        except Exception:
            pass

    await context.bot.send_message(chat_id, f"🧹 All clean! {deleted} message(s) deleted.")


# ---------------------------------------------------------------------------
# RULES
# ---------------------------------------------------------------------------
async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = RULES.get(chat_id, "Nothing here yet — ask an admin to set some with /setrules.")
    await update.message.reply_text(f"📜 <b>Group Rules</b>\n\n{esc(text)}", parse_mode="HTML")


async def setrules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    if not context.args:
        await update.message.reply_text("📝 Use: /setrules <your rules text>")
        return
    RULES[update.effective_chat.id] = " ".join(context.args)
    await update.message.reply_text("✅ Done — the group rules are now updated!")


# ---------------------------------------------------------------------------
# FILTERS (auto-reply triggers)
# ---------------------------------------------------------------------------
async def filters_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    chat_id = update.effective_chat.id
    reply = update.message.reply_to_message

    # No trigger and no reply -> show the list of active filters
    if not context.args and not reply:
        chat_filters = FILTERS.get(chat_id, {})
        if not chat_filters:
            await update.message.reply_text(
                "🧩 There are no filters set up yet.\n\n"
                "Reply to a message or sticker with <code>/filters &lt;trigger&gt;</code> to save it, "
                "or use <code>/filters &lt;trigger&gt; &lt;response text&gt;</code>.",
                parse_mode="HTML",
            )
            return
        listing = "\n".join(f"• {esc(k)}" for k in chat_filters)
        await update.message.reply_text(f"🧩 <b>Active Filters</b>\n\n{listing}", parse_mode="HTML")
        return

    # Reply-based: save whatever was replied to (text or sticker) under this trigger
    if reply:
        if not context.args:
            await update.message.reply_text(
                "⚠️ Add a trigger word after the command, e.g. <code>/filters eren</code>.", parse_mode="HTML"
            )
            return
        trigger = context.args[0].lower()
        if reply.sticker:
            FILTERS.setdefault(chat_id, {})[trigger] = {"type": "sticker", "file_id": reply.sticker.file_id}
            await update.message.reply_text(
                f"✅ Got it — I'll send that sticker whenever someone says <b>{esc(trigger)}</b>.", parse_mode="HTML"
            )
        elif reply.text:
            FILTERS.setdefault(chat_id, {})[trigger] = {"type": "text", "content": reply.text}
            await update.message.reply_text(
                f"✅ Got it — I'll reply with that message whenever someone says <b>{esc(trigger)}</b>.", parse_mode="HTML"
            )
        else:
            await update.message.reply_text("⚠️ I can only save a text message or a sticker as a filter response.")
        return

    # Inline: /filters <trigger> <response text>
    if len(context.args) < 2:
        await update.message.reply_text(
            "⚠️ Use: <code>/filters &lt;trigger&gt; &lt;response text&gt;</code>, or reply to a message/sticker "
            "with <code>/filters &lt;trigger&gt;</code>.",
            parse_mode="HTML",
        )
        return
    trigger = context.args[0].lower()
    response_text = " ".join(context.args[1:])
    FILTERS.setdefault(chat_id, {})[trigger] = {"type": "text", "content": response_text}
    await update.message.reply_text(
        f"✅ Got it — I'll reply whenever someone says <b>{esc(trigger)}</b>.", parse_mode="HTML"
    )


async def delfilters_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    if not context.args:
        await update.message.reply_text("⚠️ Use: /delfilters <trigger>")
        return
    trigger = context.args[0].lower()
    chat_id = update.effective_chat.id
    if FILTERS.get(chat_id, {}).pop(trigger, None) is not None:
        await update.message.reply_text(f"🗑️ Removed — I'll no longer respond to \"{esc(trigger)}\".", parse_mode="HTML")
    else:
        await update.message.reply_text(f"🤔 I couldn't find a filter for \"{esc(trigger)}\".", parse_mode="HTML")


async def filterlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    chat_filters = FILTERS.get(update.effective_chat.id, {})
    if not chat_filters:
        await update.message.reply_text("🧩 No filters here yet — set one up with /filters!")
        return
    listing = "\n".join(f"• {esc(k)} ({v['type']})" for k, v in chat_filters.items())
    await update.message.reply_text(
        f"🧩 <b>Active Filters</b> — {len(chat_filters)} total\n\n{listing}", parse_mode="HTML"
    )


# ---------------------------------------------------------------------------
# BLOCKLIST (banned words)
# ---------------------------------------------------------------------------
async def addblacklist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    chat_id = update.effective_chat.id
    args = list(context.args) if context.args else []

    # Optional trailing {warn} flag — also warns the sender when this entry is triggered
    warn_flag = False
    if args and args[-1].strip("{}").lower() == "warn":
        warn_flag = True
        args = args[:-1]

    reply = update.message.reply_to_message
    added_labels = []

    if reply and reply.sticker:
        STICKER_BLOCKLIST.setdefault(chat_id, {})[reply.sticker.file_unique_id] = warn_flag
        added_labels.append("that sticker")
    elif reply and reply.text:
        phrase = reply.text.strip().lower()
        BLOCKLIST.setdefault(chat_id, {})[phrase] = warn_flag
        added_labels.append(phrase)
    elif args:
        for w in args:
            BLOCKLIST.setdefault(chat_id, {})[w.lower()] = warn_flag
            added_labels.append(w.lower())
    else:
        await update.message.reply_text(
            "⚠️ Reply to a message or sticker with <code>/addblacklist</code>, or use "
            "<code>/addblacklist &lt;word1&gt; &lt;word2&gt; ...</code> — add <code>{warn}</code> at the end "
            "to also warn whoever sends it.",
            parse_mode="HTML",
        )
        return

    warn_note = " and will also warn the sender" if warn_flag else ""
    await update.message.reply_text(
        f"🚫 Blocked{warn_note}: {esc(', '.join(added_labels))}"
    )


async def removeblock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    chat_id = update.effective_chat.id
    reply = update.message.reply_to_message

    if reply and reply.sticker:
        if STICKER_BLOCKLIST.get(chat_id, {}).pop(reply.sticker.file_unique_id, None) is not None:
            await update.message.reply_text("♻️ Done — that sticker is no longer blocked.")
        else:
            await update.message.reply_text("🤔 That sticker wasn't on the blocklist.")
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ Reply to a blocked sticker with <code>/removeblock</code>, or use "
            "<code>/removeblock &lt;word1&gt; &lt;word2&gt; ...</code>.",
            parse_mode="HTML",
        )
        return

    removed = []
    for w in context.args:
        w = w.lower()
        if w in BLOCKLIST.get(chat_id, {}):
            del BLOCKLIST[chat_id][w]
            removed.append(w)
    if removed:
        await update.message.reply_text(f"♻️ Unblocked: {esc(', '.join(removed))}", parse_mode="HTML")
    else:
        await update.message.reply_text("🤔 None of those were on the blocklist to begin with.")


async def is_exempt_from_blocklist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Group admins/owner and explicitly-approved users are exempt from the
    word and sticker blocklist."""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if user_id in APPROVED_USERS.get(chat_id, set()):
        return True
    return await is_admin(update, context, user_id)


async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    target, _ = await get_target(update, context)
    if not target:
        await update.message.reply_text(NO_TARGET_TEXT, parse_mode="HTML")
        return
    chat_id = update.effective_chat.id
    APPROVED_USERS.setdefault(chat_id, set()).add(target.id)
    await update.message.reply_text(
        f"✅ {mention(target)} is now approved — their messages will skip the blocklist filter.",
        parse_mode="HTML",
    )


async def unapprove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    target, _ = await get_target(update, context)
    if not target:
        await update.message.reply_text(NO_TARGET_TEXT, parse_mode="HTML")
        return
    chat_id = update.effective_chat.id
    if target.id in APPROVED_USERS.get(chat_id, set()):
        APPROVED_USERS[chat_id].discard(target.id)
        await update.message.reply_text(f"♻️ {mention(target)} is no longer approved.", parse_mode="HTML")
    else:
        await update.message.reply_text(f"⚠️ {mention(target)} wasn't approved to begin with.", parse_mode="HTML")


async def blocklist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    chat_id = update.effective_chat.id
    words = BLOCKLIST.get(chat_id, {})
    stickers = STICKER_BLOCKLIST.get(chat_id, {})

    if not words and not stickers:
        await update.message.reply_text("✨ Nothing blocked here — the blocklist is squeaky clean.")
        return

    lines = [f"🚫 <b>Blocklist</b> — {len(words)} word(s), {len(stickers)} sticker(s)\n"]
    if words:
        lines.append("<b>Words:</b>")
        lines += [f"• {esc(w)}" + (" ⚠️" if warn else "") for w, warn in words.items()]
    if stickers:
        warn_count = sum(1 for v in stickers.values() if v)
        lines.append(f"\n<b>Stickers:</b> {len(stickers)} blocked ({warn_count} also warn the sender)")
    lines.append("\n⚠️ = also warns the sender when triggered")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ---------------------------------------------------------------------------
# WELCOME MESSAGE
# ---------------------------------------------------------------------------
async def setwelcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    chat_id = update.effective_chat.id
    reply = update.message.reply_to_message

    if reply:
        if reply.sticker:
            WELCOME[chat_id] = {"type": "sticker", "file_id": reply.sticker.file_id}
            await update.message.reply_text("✅ Perfect — new members will now be greeted with that sticker!")
            return
        if reply.text:
            WELCOME[chat_id] = {"type": "text", "content": reply.text}
            await update.message.reply_text("✅ All set — new members will see that welcome message!")
            return
        await update.message.reply_text("🤔 I can only use a text message or a sticker as the welcome message.")
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ Reply to a message or sticker with <code>/setwelcome</code>, or use "
            "<code>/setwelcome &lt;message&gt;</code> — you can use <code>{name}</code> or "
            "<code>{username}</code> to mention the new member.",
            parse_mode="HTML",
        )
        return

    WELCOME[chat_id] = {"type": "text", "content": " ".join(context.args)}
    await update.message.reply_text("✅ All set — new members will see that welcome message!")


async def delsetwelcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    chat_id = update.effective_chat.id
    if WELCOME.pop(chat_id, None) is not None:
        await update.message.reply_text("🗑️ Done — the welcome message has been cleared.")
    else:
        await update.message.reply_text("🤔 There wasn't a welcome message set to begin with.")


async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    welcome = WELCOME.get(chat_id)
    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            continue  # don't welcome the bot itself

        if welcome and welcome["type"] == "sticker":
            try:
                await update.message.reply_sticker(welcome["file_id"])
            except Exception as e:
                logger.warning(f"Welcome sticker send failed: {e}")
            continue

        if welcome and welcome["type"] == "text":
            username_display = f"@{esc(member.username)}" if member.username else mention(member)
            text = (
                esc(welcome["content"])
                .replace("{name}", esc(member.full_name))
                .replace("{username}", username_display)
            )
        else:
            text = f"🎉 Welcome to the group, {mention(member)}! We're glad to have you here."
        await update.message.reply_text(text, parse_mode="HTML")


# ---------------------------------------------------------------------------
# TRUTH / DARE
# ---------------------------------------------------------------------------
async def truth_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = random.choice(TRUTH_QUESTIONS)
    await update.message.reply_text(f"🤔 <b>Truth:</b>\n{esc(q)}", parse_mode="HTML")


async def dare_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = random.choice(DARE_CHALLENGES)
    await update.message.reply_text(f"🔥 <b>Dare:</b>\n{esc(d)}", parse_mode="HTML")


# ---------------------------------------------------------------------------
# TRANSLATE
# ---------------------------------------------------------------------------
async def translate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("🌐 Use: /tr <language> (reply to a message) or /tr <language> <text>")
        return
    target_lang = context.args[0]

    if update.message.reply_to_message and update.message.reply_to_message.text:
        source_text = update.message.reply_to_message.text
    elif len(context.args) > 1:
        source_text = " ".join(context.args[1:])
    else:
        await update.message.reply_text("🌐 Reply to a message to translate, or add some text after the language.")
        return

    if not gemini_client:
        await update.message.reply_text("⚙️ Translation isn't configured yet — GEMINI_API_KEY needs to be set.")
        return

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[{"role": "user", "parts": [{"text": source_text}]}],
            config={
                "system_instruction": f"Translate the user's message into {target_lang}. Reply with ONLY the translated text, nothing else.",
                "max_output_tokens": 500,
            },
        )
        translated = response.text or "..."
        await update.message.reply_text(f"🌐 <b>Translation ({esc(target_lang)}):</b>\n{esc(translated)}", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Translate error: {e}")
        await update.message.reply_text(friendly_gemini_error(e))


async def google_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("🔎 Use: /google <your question>")
        return
    if not gemini_client:
        await update.message.reply_text("⚙️ Search isn't configured yet — GEMINI_API_KEY needs to be set.")
        return

    query = " ".join(context.args)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        grounding_tool = genai_types.Tool(google_search=genai_types.GoogleSearch())
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=query,
            config=genai_types.GenerateContentConfig(tools=[grounding_tool], max_output_tokens=600),
        )
        answer = response.text or "I couldn't find a clear answer for that."
        await update.message.reply_text(
            f"🔎 <b>{esc(query)}</b>\n\n{esc(answer)}", parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Google search error: {e}")
        await update.message.reply_text(friendly_gemini_error(e))


# ---------------------------------------------------------------------------
# WORD CHAIN GAME (turn-based, multiplayer, with elimination)
# ---------------------------------------------------------------------------
async def is_real_word(word: str) -> bool:
    """Checks a word against the free dictionaryapi.dev API. Fails open
    (treats the word as valid) if the API is unreachable, so the game
    doesn't break when there's no internet access."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}")
            return resp.status_code == 200
    except Exception as e:
        logger.warning(f"Dictionary API check failed, allowing word: {e}")
        return True


async def schedule_game_timeout(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    state = GAME_STATE.get(chat_id)
    if not state or state["phase"] != "playing":
        return
    old_job = state.get("job")
    if old_job:
        old_job.schedule_removal()
    state["job"] = context.job_queue.run_once(
        game_timeout, state["time_limit"], chat_id=chat_id, name=f"game_{chat_id}"
    )


async def game_timeout(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    state = GAME_STATE.get(chat_id)
    if not state or state["phase"] != "playing":
        return

    players = state["players"]
    if not players:
        GAME_STATE.pop(chat_id, None)
        return

    idx = state["current_index"]
    eliminated = players.pop(idx)
    await context.bot.send_message(
        chat_id,
        f"⏰ {mention(eliminated)} ran out of time and is <b>eliminated</b>!",
        parse_mode="HTML",
    )

    if len(players) <= 1:
        if players:
            await context.bot.send_message(
                chat_id, f"🏆 {mention(players[0])} wins the Word Chain game! 🎉", parse_mode="HTML"
            )
        else:
            await context.bot.send_message(chat_id, "🏁 Game over — no players left!")
        GAME_STATE.pop(chat_id, None)
        return

    if idx >= len(players):
        idx = 0
    state["current_index"] = idx
    next_player = players[idx]
    letter_hint = f"\"{state['last_word'][-1].upper()}\"" if state["last_word"] else "any letter"
    await context.bot.send_message(
        chat_id,
        f"🎯 Next turn: {mention(next_player)} — send a word starting with {letter_hint} "
        f"({state['min_length']}+ letters) within {state['time_limit']}s!",
        parse_mode="HTML",
    )
    await schedule_game_timeout(context, chat_id)


async def game_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = GAME_STATE.get(chat_id)

    if not state:
        GAME_STATE[chat_id] = {
            "phase": "lobby", "players": [], "current_index": 0,
            "used_words": set(), "last_word": None, "round": 0,
            "time_limit": 30, "min_length": 3, "job": None,
        }
        await update.message.reply_text(
            "🔗 <b>Word Chain Game — Lobby Open!</b>\n\n"
            "Type /join to enter.\n"
            "Once at least 2 players have joined, send /game again to start!",
            parse_mode="HTML",
        )
        return

    if state["phase"] == "lobby":
        if len(state["players"]) < 2:
            await update.message.reply_text("🙋 I need at least 2 players before we can start — use /join to hop in!")
            return
        state["phase"] = "playing"
        random.shuffle(state["players"])
        state["current_index"] = 0
        current = state["players"][0]
        await update.message.reply_text(
            f"🚀 <b>Game Started!</b> {len(state['players'])} players are in.\n\n"
            f"📋 Rules: each word must start with the last letter of the previous word, "
            f"no repeats, and it gets harder every few rounds (less time, longer words).\n\n"
            f"🎯 First turn: {mention(current)} — send any word within {state['time_limit']}s!",
            parse_mode="HTML",
        )
        await schedule_game_timeout(context, chat_id)
        return

    # phase == "playing" — game is already running, point them to /endgame
    await update.message.reply_text(
        f"⚠️ A game is already in progress (round {state['round']}). "
        f"An admin can use /endgame to stop it early."
    )


async def endgame_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    chat_id = update.effective_chat.id
    state = GAME_STATE.pop(chat_id, None)
    if not state:
        await update.message.reply_text("🤔 There's no game running at the moment.")
        return
    job = state.get("job")
    if job:
        job.schedule_removal()
    if state["phase"] == "playing":
        await update.message.reply_text(f"🛑 Word Chain game ended by an admin at round {state['round']}.")
    else:
        await update.message.reply_text("🛑 Lobby closed by an admin.")


async def join_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = GAME_STATE.get(chat_id)
    if not state or state["phase"] != "lobby":
        await update.message.reply_text("🤔 There's no open lobby right now — use /game to start one!")
        return
    user = update.effective_user
    if any(p["id"] == user.id for p in state["players"]):
        await update.message.reply_text("😄 You're already in the lobby — just sit tight!")
        return
    player = {"id": user.id, "full_name": user.full_name, "username": user.username}
    state["players"].append(player)
    await update.message.reply_text(f"✅ {mention(player)} joined! ({len(state['players'])} players in lobby)", parse_mode="HTML")


async def handle_game_turn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Returns True if the message was consumed as a game move (valid or invalid)."""
    chat_id = update.effective_chat.id
    state = GAME_STATE.get(chat_id)
    if not state or state["phase"] != "playing" or not state["players"]:
        return False

    current = state["players"][state["current_index"]]
    if update.effective_user.id != current["id"]:
        return False  # not this player's turn — let the message pass through normally

    text = update.message.text.strip().lower()
    if not text.isalpha():
        return False  # not a word attempt at all

    if text in state["used_words"]:
        await update.message.reply_text(f"♻️ \"{esc(text)}\" has already been used — try something fresh!", parse_mode="HTML")
        return True

    if len(text) < state["min_length"]:
        await update.message.reply_text(f"📏 That word needs to be at least {state['min_length']} letters long!")
        return True

    if state["last_word"] and text[0] != state["last_word"][-1]:
        await update.message.reply_text(f"🔤 Your word needs to start with \"{state['last_word'][-1].upper()}\"!")
        return True

    if not await is_real_word(text):
        await update.message.reply_text(f"⚠️ \"{esc(text)}\" doesn't look like a real English word — try again!", parse_mode="HTML")
        return True

    # valid word — advance turn
    state["used_words"].add(text)
    state["last_word"] = text
    state["round"] += 1
    state["time_limit"] = max(8, state["time_limit"] - 2)
    if state["round"] % 3 == 0:
        state["min_length"] += 1

    state["current_index"] = (state["current_index"] + 1) % len(state["players"])
    next_player = state["players"][state["current_index"]]

    await update.message.reply_text(
        f"✅ Round {state['round']}! Next turn: {mention(next_player)} — "
        f"word must start with \"{text[-1].upper()}\" ({state['min_length']}+ letters) — ⏱️ {state['time_limit']}s",
        parse_mode="HTML",
    )
    await schedule_game_timeout(context, chat_id)
    return True


# ---------------------------------------------------------------------------
# AI CHAT (Google Gemini)
# ---------------------------------------------------------------------------
async def should_respond_as_chatbot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    message = update.message
    if chat.type == ChatType.PRIVATE:
        return True
    bot_username = context.bot.username
    if message.reply_to_message and message.reply_to_message.from_user.id == context.bot.id:
        return True
    if bot_username and message.text and f"@{bot_username}" in message.text:
        return True
    return False


async def chat_with_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await should_respond_as_chatbot(update, context):
        return
    if not gemini_client:
        await update.message.reply_text("⚙️ My chat feature isn't configured yet — GEMINI_API_KEY needs to be set.")
        return

    chat_id = update.effective_chat.id
    user_text = update.message.text.replace(f"@{context.bot.username}", "").strip()

    if not user_text:
        await update.message.reply_text("👋 Haan bolo, kya chahiye?")
        return

    history = CHAT_HISTORY.setdefault(chat_id, [])
    history.append({"role": "user", "parts": [{"text": user_text}]})
    history[:] = history[-MAX_HISTORY_MESSAGES:]

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=history,
            config={
                "system_instruction": "Tum ek friendly Telegram group chatbot ho. Chhote, natural replies do (Hinglish me baat karo).",
                "max_output_tokens": 500,
            },
        )
        reply_text = response.text or "..."
        history.append({"role": "model", "parts": [{"text": reply_text}]})
        history[:] = history[-MAX_HISTORY_MESSAGES:]
        await update.message.reply_text(reply_text)
    except Exception as e:
        logger.error(f"AI chat error: {e}")
        await update.message.reply_text(friendly_gemini_error(e))


# ---------------------------------------------------------------------------
# STICKER ECHO
# ---------------------------------------------------------------------------
async def sticker_echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.sticker:
        return

    chat_id = update.effective_chat.id
    sticker = update.message.sticker

    blocked_stickers = STICKER_BLOCKLIST.get(chat_id, {})
    if sticker.file_unique_id in blocked_stickers and not await is_exempt_from_blocklist(update, context):
        try:
            await update.message.delete()
        except Exception as e:
            logger.warning(f"Sticker blocklist delete failed: {e}")
        await context.bot.send_message(chat_id, "🚫 That sticker was removed — it's on the blocklist.")
        if blocked_stickers[sticker.file_unique_id]:
            await apply_warning(context, chat_id, update.effective_user, "sent a blocked sticker")
        return

    if update.effective_chat.type != ChatType.PRIVATE:
        # In groups, only react if this sticker was sent as a reply to the bot
        reply = update.message.reply_to_message
        if not reply or not reply.from_user or reply.from_user.id != context.bot.id:
            return

    try:
        await update.message.reply_sticker(sticker.file_id)
    except Exception as e:
        logger.error(f"Sticker echo error: {e}")


# ---------------------------------------------------------------------------
# MASTER TEXT HANDLER (game -> blocklist -> filters -> chatbot, in order)
# ---------------------------------------------------------------------------
async def handle_group_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text
    if text.startswith("/"):
        return
    chat_id = update.effective_chat.id

    try:
        if await handle_game_turn(update, context):
            return

        lowered = text.lower()
        if not await is_exempt_from_blocklist(update, context):
            for word, warn_flag in BLOCKLIST.get(chat_id, {}).items():
                if re.search(rf"\b{re.escape(word)}\b", lowered):
                    try:
                        await update.message.delete()
                    except Exception as e:
                        logger.warning(f"Blocklist delete failed: {e}")
                    await context.bot.send_message(chat_id, "🚫 That message was removed — it contained a blocked word.")
                    if warn_flag:
                        await apply_warning(context, chat_id, update.effective_user, "used a blocked word")
                    return

        for trigger, response in FILTERS.get(chat_id, {}).items():
            if re.search(rf"\b{re.escape(trigger)}\b", lowered):
                if response["type"] == "sticker":
                    await update.message.reply_sticker(response["file_id"])
                else:
                    await update.message.reply_text(response["content"])
                return

        await chat_with_ai(update, context)
    except Exception as e:
        logger.error(f"handle_group_text crashed (message ignored, chat continues normally): {e}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
BOT_COMMANDS = [
    BotCommand("start", "👋 Show welcome message and buttons"),
    BotCommand("commands", "📋 List all bot commands"),
    BotCommand("info", "👤 View user info"),
    BotCommand("admins", "👑 List group admins and owner"),
    BotCommand("developer", "👨‍💻 Meet the bot's developer"),
    BotCommand("ping", "🏓 Check if the bot is online"),
    BotCommand("kick", "👢 Remove a user (can rejoin)"),
    BotCommand("ban", "🔨 Permanently ban a user"),
    BotCommand("unban", "✅ Unban a user"),
    BotCommand("mute", "🔇 Mute a user"),
    BotCommand("unmute", "🔊 Unmute a user"),
    BotCommand("warn", "⚠️ Warn a user"),
    BotCommand("warnings", "📋 Check a user's warnings"),
    BotCommand("resetwarns", "♻️ Reset a user's warnings"),
    BotCommand("promote", "⬆️ Make a user an admin"),
    BotCommand("demote", "⬇️ Remove admin rights"),
    BotCommand("pin", "📌 Pin the replied message"),
    BotCommand("unpin", "📍 Remove pinned message"),
    BotCommand("purge", "🧹 Delete messages from reply point"),
    BotCommand("rules", "📜 View group rules"),
    BotCommand("setrules", "📝 Set group rules"),
    BotCommand("filters", "🧩 Add an auto-reply filter"),
    BotCommand("delfilters", "🗑️ Remove an auto-reply filter"),
    BotCommand("filterlist", "📋 List all active filters"),
    BotCommand("addblacklist", "🚫 Block words or a sticker"),
    BotCommand("removeblock", "♻️ Unblock a word or sticker"),
    BotCommand("approve", "✅ Exempt a user from the blocklist"),
    BotCommand("unapprove", "♻️ Remove a user's blocklist exemption"),
    BotCommand("blocklist", "📋 List all blocked words/stickers"),
    BotCommand("setwelcome", "💬 Set the welcome message"),
    BotCommand("delsetwelcome", "🗑️ Remove the welcome message"),
    BotCommand("truth", "🤔 Get a random truth question"),
    BotCommand("dare", "🔥 Get a random dare challenge"),
    BotCommand("tr", "🌐 Translate a message"),
    BotCommand("google", "🔎 Search Google for an answer"),
    BotCommand("game", "🔗 Open/start the word chain game"),
    BotCommand("endgame", "🛑 Force-end the game (admin only)"),
    BotCommand("join", "🙋 Join the word chain lobby"),
]


async def post_init(app: Application):
    await app.bot.set_my_commands(BOT_COMMANDS)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catches any exception not already handled inside a specific handler,
    so one bad update can never take the whole bot down. Optionally reports
    the traceback to the developer for debugging."""
    logger.error("Unhandled exception while processing an update:", exc_info=context.error)

    if DEVELOPER_CHAT_ID:
        try:
            tb_string = "".join(
                traceback.format_exception(None, context.error, context.error.__traceback__)
            )
            await context.bot.send_message(
                int(DEVELOPER_CHAT_ID),
                f"⚠️ <b>Bot error</b>\n\n<code>{esc(tb_string[-3500:])}</code>",
                parse_mode="HTML",
            )
        except Exception:
            pass  # never let error reporting itself crash anything


async def track_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Silently records every chat the bot has seen a message from, so
    /broadcast knows where it can deliver an announcement."""
    if update.effective_chat:
        KNOWN_CHATS.add(update.effective_chat.id)


def is_owner(update: Update) -> bool:
    if not DEVELOPER_CHAT_ID:
        return False
    return str(update.effective_user.id) == str(DEVELOPER_CHAT_ID).strip()


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.message.reply_text("❌ This command is reserved for my owner.")
        return

    reply = update.message.reply_to_message
    sent, failed = 0, 0

    async def send_to_all(send_fn):
        nonlocal sent, failed
        for chat_id in list(KNOWN_CHATS):
            try:
                await send_fn(chat_id)
                sent += 1
            except Exception as e:
                failed += 1
                logger.warning(f"Broadcast to {chat_id} failed: {e}")

    if reply and reply.sticker:
        await send_to_all(lambda chat_id: context.bot.send_sticker(chat_id, reply.sticker.file_id))
    elif reply and reply.text:
        text = reply.text
        await send_to_all(
            lambda chat_id: context.bot.send_message(
                chat_id, f"📢 <b>Announcement</b>\n\n{esc(text)}", parse_mode="HTML"
            )
        )
    elif context.args:
        text = " ".join(context.args)
        await send_to_all(
            lambda chat_id: context.bot.send_message(
                chat_id, f"📢 <b>Announcement</b>\n\n{esc(text)}", parse_mode="HTML"
            )
        )
    else:
        await update.message.reply_text(
            "📢 Reply to a message or sticker with /broadcast, or use /broadcast <message>."
        )
        return

    await update.message.reply_text(f"📢 Broadcast sent — delivered to {sent} chat(s), failed for {failed}.")


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.message.reply_text("❌ This command is reserved for my owner.")
        return

    # Telegram convention: group/supergroup/channel chat IDs are negative,
    # private DM chat IDs are positive (equal to the user's own ID).
    groups = sum(1 for c in KNOWN_CHATS if c < 0)
    dms = sum(1 for c in KNOWN_CHATS if c > 0)

    await update.message.reply_text(
        f"📊 <b>Bot Stats</b>\n\n"
        f"👥 Groups I'm active in: <b>{groups}</b>\n"
        f"💬 Users who've DM'd me: <b>{dms}</b>\n"
        f"📡 Total known chats: <b>{len(KNOWN_CHATS)}</b>",
        parse_mode="HTML",
    )


def main():
    if BOT_TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":
        print("⚠️  Pehle BOT_TOKEN set karo (script me ya TELEGRAM_BOT_TOKEN env var me)!")
        return

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("commands", commands_cmd))
    app.add_handler(CallbackQueryHandler(show_commands_callback, pattern="^show_commands$"))
    app.add_handler(CommandHandler("info", info))
    app.add_handler(CommandHandler("admins", admins_cmd))
    app.add_handler(CommandHandler("developer", developer))
    app.add_handler(CommandHandler("ping", ping_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))

    # Runs in its own group so it never blocks the normal command/message handlers
    app.add_handler(MessageHandler(filters.ALL, track_chat), group=-1)

    app.add_handler(CommandHandler("kick", kick))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))

    app.add_handler(CommandHandler("mute", mute))
    app.add_handler(CommandHandler("unmute", unmute))

    app.add_handler(CommandHandler("warn", warn))
    app.add_handler(CommandHandler("warnings", warnings_cmd))
    app.add_handler(CommandHandler("resetwarns", resetwarns))

    app.add_handler(CommandHandler("promote", promote))
    app.add_handler(CommandHandler("demote", demote))

    app.add_handler(CommandHandler("pin", pin))
    app.add_handler(CommandHandler("unpin", unpin))
    app.add_handler(CommandHandler("purge", purge))

    app.add_handler(CommandHandler("rules", rules))
    app.add_handler(CommandHandler("setrules", setrules))

    app.add_handler(CommandHandler("filters", filters_cmd))
    app.add_handler(CommandHandler("delfilters", delfilters_cmd))
    app.add_handler(CommandHandler("filterlist", filterlist_cmd))

    app.add_handler(CommandHandler("addblacklist", addblacklist_cmd))
    app.add_handler(CommandHandler("addblocklist", addblacklist_cmd))  # backward-compat alias
    app.add_handler(CommandHandler("removeblock", removeblock_cmd))
    app.add_handler(CommandHandler("approve", approve_cmd))
    app.add_handler(CommandHandler("unapprove", unapprove_cmd))
    app.add_handler(CommandHandler("blocklist", blocklist_cmd))

    app.add_handler(CommandHandler("setwelcome", setwelcome_cmd))
    app.add_handler(CommandHandler("delsetwelcome", delsetwelcome_cmd))

    app.add_handler(CommandHandler("truth", truth_cmd))
    app.add_handler(CommandHandler("dare", dare_cmd))
    app.add_handler(CommandHandler("tr", translate_cmd))
    app.add_handler(CommandHandler("google", google_cmd))
    app.add_handler(CommandHandler("game", game_cmd))
    app.add_handler(CommandHandler("endgame", endgame_cmd))
    app.add_handler(CommandHandler("join", join_cmd))

    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))
    app.add_handler(MessageHandler(filters.Sticker.ALL, sticker_echo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_group_text))

    app.add_error_handler(error_handler)

    print("🤖 Bot chalu ho gaya...")
    app.run_polling()


if __name__ == "__main__":
    main()
