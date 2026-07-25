"""
Telegram Group Admin Bot + AI Chatbot
--------------------------------------
Admin Commands: /kick /ban /unban /mute /unmute /warn /warnings /resetwarns
                /promote /demote /pin /unpin /purge /info /rules /setrules
                /filters /delfilters /addblocklist /removeblock
                /setwelcome /delsetwelcome
Fun Commands:   /truth /dare /tr <language> /game (word chain)
Chat Feature:   AI chat (Google Gemini, free) + sticker echo

Most admin commands accept EITHER:
  - a reply to the target user's message, e.g. reply + "/ban"
  - OR "@username" as the first argument, e.g. "/ban @someuser spamming"

Setup:
1. pip install -r requirements.txt
2. Set TELEGRAM_BOT_TOKEN env var (bot token from BotFather)
3. Set GEMINI_API_KEY env var (free, from aistudio.google.com/apikey)
4. Add the bot to your group and make it an ADMIN with these rights:
   - Ban users, Delete messages, Pin messages, Add new admins (for /promote)
5. Run: python group_admin_bot.py
"""

import logging
import os
import random
from datetime import timedelta

from google import genai
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
FILTERS: dict[int, dict[str, str]] = {}     # chat_id -> {trigger: response}
BLOCKLIST: dict[int, set] = {}              # chat_id -> {blocked words}
WELCOME: dict[int, str] = {}                # chat_id -> welcome template
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


def mention(obj) -> str:
    """Returns a clickable @username mention if available, else a bold name.
    Accepts telegram.User, TargetUser, or a plain dict with id/full_name/username."""
    if isinstance(obj, dict):
        username = obj.get("username")
        full_name = obj.get("full_name") or "Player"
    else:
        username = getattr(obj, "username", None)
        full_name = getattr(obj, "full_name", None) or "User"
    if username:
        return f"@{username}"
    return f"*{full_name}*"


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
        await update.message.reply_text("❌ Only group admins can use this command.")
        return False
    return True


def get_target_user(update: Update):
    if update.message.reply_to_message:
        return update.message.reply_to_message.from_user
    return None


async def get_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resolves the command's target user via reply OR a leading @username
    argument. Returns (target_or_None, remaining_args)."""
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
            return None, args[1:]
    reply_user = get_target_user(update)
    if reply_user:
        return reply_user, args
    return None, args


async def ensure_bot_is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    bot_member = await context.bot.get_chat_member(update.effective_chat.id, context.bot.id)
    if bot_member.status != ChatMemberStatus.ADMINISTRATOR:
        await update.message.reply_text(
            "⚠️ Please make me an ADMIN first (with ban/mute/pin permissions), then this command will work."
        )
        return False
    return True


NO_TARGET_TEXT = (
    "⚠️ Specify a user — reply to their message, or use *@username* right after the command."
)


# ---------------------------------------------------------------------------
# BASIC COMMANDS
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
def build_commands_text() -> str:
    return (
        "*📋 All Commands*\n\n"
        "*🛡️ Moderation*\n"
        "👢 /kick — remove a user (can rejoin)\n"
        "🔨 /ban — permanently ban a user\n"
        "✅ /unban <user_id or @username> — unban a user\n"
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
        "*⚙️ Group Setup*\n"
        "📜 /rules — view group rules\n"
        "📝 /setrules <text> — set group rules\n"
        "🧩 /filters <trigger> <response> — add an auto-reply\n"
        "🗑️ /delfilters <trigger> — remove an auto-reply\n"
        "🚫 /addblocklist <words> — block words from being sent\n"
        "♻️ /removeblock <words> — unblock words\n"
        "💬 /setwelcome <text> — set a welcome message ({name} = user)\n"
        "🗑️ /delsetwelcome — remove the welcome message\n\n"
        "*🎉 Fun*\n"
        "🤔 /truth — random truth question\n"
        "🔥 /dare — random dare challenge\n"
        "🌐 /tr <language> — translate a replied message\n"
        "🔗 /game — open a word chain lobby (send again to start once 2+ joined)\n"
        "🙋 /join — join an open lobby\n\n"
        "*ℹ️ Other*\n"
        "👤 /info — view a user's info\n"
        "👨‍💻 /developer — meet the bot's developer\n\n"
        "Targeting a user: *reply* to their message, or add *@username* right after the command."
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
        "👋 *Hello! I'm your group's management assistant.*\n\n"
        "I handle moderation (kick/ban/mute/warn), group setup (filters, blocklist, "
        "welcome messages), and fun stuff (truth/dare, translation, a word chain game) — "
        "all in one bot.\n\n"
        "Tap *Add me to your Group* below to get started, then make me an *ADMIN* "
        "so moderation commands work properly.",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def commands_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(build_commands_text(), parse_mode="Markdown")


async def show_commands_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(build_commands_text(), parse_mode="Markdown")


async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target, _ = await get_target(update, context)
    target = target or update.effective_user
    admin_status = await is_admin(update, context, target.id)
    text = (
        f"ℹ️ *User Info*\n\n"
        f"👤 Name: {target.full_name}\n"
        f"🆔 ID: {target.id}\n"
        f"🔗 Username: @{target.username if target.username else 'N/A'}\n"
        f"🛡️ Admin: {'Yes' if admin_status else 'No'}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def developer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👨‍💻 *About my developer*\n\n"
        "I was built and maintained by @liesworlds ✨\n"
        "For new features, feedback, or bug reports — feel free to reach out directly!",
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# KICK / BAN / UNBAN
# ---------------------------------------------------------------------------
async def kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    target, _ = await get_target(update, context)
    if not target:
        await update.message.reply_text(NO_TARGET_TEXT, parse_mode="Markdown")
        return
    chat_id = update.effective_chat.id
    try:
        await context.bot.ban_chat_member(chat_id, target.id)
        await context.bot.unban_chat_member(chat_id, target.id)
        await update.message.reply_text(f"👢 {mention(target)} has been kicked from the group.", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Kick failed: {e}")


async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    target, args = await get_target(update, context)
    if not target:
        await update.message.reply_text(NO_TARGET_TEXT, parse_mode="Markdown")
        return
    reason = " ".join(args) if args else "No reason given"
    chat_id = update.effective_chat.id
    try:
        await context.bot.ban_chat_member(chat_id, target.id)
        await update.message.reply_text(f"🔨 {mention(target)} has been banned.\n📝 Reason: {reason}", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Ban failed: {e}")


async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    if not context.args:
        await update.message.reply_text("⚠️ Use: /unban <user_id> or /unban @username")
        return
    identifier = context.args[0]
    try:
        if identifier.startswith("@"):
            chat = await context.bot.get_chat(identifier)
            user_id = chat.id
        else:
            user_id = int(identifier)
        await context.bot.unban_chat_member(update.effective_chat.id, user_id, only_if_banned=True)
        await update.message.reply_text(f"✅ User {identifier} has been unbanned.")
    except Exception as e:
        await update.message.reply_text(f"❌ Unban failed: {e}")


# ---------------------------------------------------------------------------
# MUTE / UNMUTE
# ---------------------------------------------------------------------------
async def mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    target, args = await get_target(update, context)
    if not target:
        await update.message.reply_text(NO_TARGET_TEXT, parse_mode="Markdown")
        return

    minutes = None
    if args:
        try:
            minutes = int(args[0])
        except ValueError:
            await update.message.reply_text("⚠️ Enter minutes as a number. Example: /mute 30")
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
        await update.message.reply_text(f"🔇 {mention(target)} has been muted{duration_text}.", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Mute failed: {e}")


async def unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    target, _ = await get_target(update, context)
    if not target:
        await update.message.reply_text(NO_TARGET_TEXT, parse_mode="Markdown")
        return

    permissions = ChatPermissions(
        can_send_messages=True, can_send_audios=True, can_send_documents=True,
        can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
        can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
        can_add_web_page_previews=True,
    )
    try:
        await context.bot.restrict_chat_member(update.effective_chat.id, target.id, permissions=permissions)
        await update.message.reply_text(f"🔊 {mention(target)} has been unmuted.", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Unmute failed: {e}")


# ---------------------------------------------------------------------------
# WARNINGS
# ---------------------------------------------------------------------------
async def warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    target, args = await get_target(update, context)
    if not target:
        await update.message.reply_text(NO_TARGET_TEXT, parse_mode="Markdown")
        return

    reason = " ".join(args) if args else "No reason given"
    chat_id = update.effective_chat.id
    WARNINGS.setdefault(chat_id, {})
    WARNINGS[chat_id][target.id] = WARNINGS[chat_id].get(target.id, 0) + 1
    count = WARNINGS[chat_id][target.id]

    await update.message.reply_text(
        f"⚠️ {mention(target)} has been warned. ({count}/{MAX_WARNINGS})\n📝 Reason: {reason}",
        parse_mode="Markdown",
    )

    if count >= MAX_WARNINGS:
        try:
            await context.bot.ban_chat_member(chat_id, target.id)
            WARNINGS[chat_id][target.id] = 0
            await update.message.reply_text(
                f"🔨 {mention(target)} reached {MAX_WARNINGS} warnings and has been banned.",
                parse_mode="Markdown",
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Auto-ban failed: {e}")


async def warnings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target, _ = await get_target(update, context)
    target = target or update.effective_user
    chat_id = update.effective_chat.id
    count = WARNINGS.get(chat_id, {}).get(target.id, 0)
    await update.message.reply_text(f"📋 {mention(target)}'s warnings: {count}/{MAX_WARNINGS}", parse_mode="Markdown")


async def resetwarns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    target, _ = await get_target(update, context)
    if not target:
        await update.message.reply_text(NO_TARGET_TEXT, parse_mode="Markdown")
        return
    chat_id = update.effective_chat.id
    WARNINGS.setdefault(chat_id, {})[target.id] = 0
    await update.message.reply_text(f"♻️ {mention(target)}'s warnings have been reset.", parse_mode="Markdown")


# ---------------------------------------------------------------------------
# PROMOTE / DEMOTE
# ---------------------------------------------------------------------------
async def promote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    target, _ = await get_target(update, context)
    if not target:
        await update.message.reply_text(NO_TARGET_TEXT, parse_mode="Markdown")
        return
    try:
        await context.bot.promote_chat_member(
            update.effective_chat.id, target.id,
            can_change_info=True, can_delete_messages=True, can_invite_users=True,
            can_restrict_members=True, can_pin_messages=True, can_promote_members=False,
        )
        await update.message.reply_text(f"⬆️ {mention(target)} has been made an admin.", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Promote failed: {e}")


async def demote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    target, _ = await get_target(update, context)
    if not target:
        await update.message.reply_text(NO_TARGET_TEXT, parse_mode="Markdown")
        return
    try:
        await context.bot.promote_chat_member(
            update.effective_chat.id, target.id,
            can_change_info=False, can_delete_messages=False, can_invite_users=False,
            can_restrict_members=False, can_pin_messages=False, can_promote_members=False,
        )
        await update.message.reply_text(f"⬇️ {mention(target)}'s admin rights have been removed.", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Demote failed: {e}")


# ---------------------------------------------------------------------------
# PIN / UNPIN / PURGE
# ---------------------------------------------------------------------------
async def pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("⚠️ Reply to the message you want to pin with /pin.")
        return
    try:
        await context.bot.pin_chat_message(update.effective_chat.id, update.message.reply_to_message.message_id)
        await update.message.reply_text("📌 Message pinned.")
    except Exception as e:
        await update.message.reply_text(f"❌ Pin failed: {e}")


async def unpin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    try:
        await context.bot.unpin_chat_message(update.effective_chat.id)
        await update.message.reply_text("📍 Message unpinned.")
    except Exception as e:
        await update.message.reply_text(f"❌ Unpin failed: {e}")


async def purge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("⚠️ Reply to the message from where you want to delete onward with /purge.")
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

    await context.bot.send_message(chat_id, f"🧹 {deleted} messages deleted.")


# ---------------------------------------------------------------------------
# RULES
# ---------------------------------------------------------------------------
async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = RULES.get(chat_id, "No rules have been set for this group yet.")
    await update.message.reply_text(f"📜 *Group Rules*\n\n{text}", parse_mode="Markdown")


async def setrules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    if not context.args:
        await update.message.reply_text("⚠️ Use: /setrules <rules text>")
        return
    RULES[update.effective_chat.id] = " ".join(context.args)
    await update.message.reply_text("✅ Rules have been set.")


# ---------------------------------------------------------------------------
# FILTERS (auto-reply triggers)
# ---------------------------------------------------------------------------
async def filters_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    chat_id = update.effective_chat.id
    if not context.args:
        chat_filters = FILTERS.get(chat_id, {})
        if not chat_filters:
            await update.message.reply_text(
                "🧩 No filters set yet.\nUse: /filters <trigger> <response text>"
            )
            return
        listing = "\n".join(f"• {k}" for k in chat_filters)
        await update.message.reply_text(f"🧩 *Active Filters*\n\n{listing}", parse_mode="Markdown")
        return
    if len(context.args) < 2:
        await update.message.reply_text("⚠️ Use: /filters <trigger> <response text>")
        return
    trigger = context.args[0].lower()
    response = " ".join(context.args[1:])
    FILTERS.setdefault(chat_id, {})[trigger] = response
    await update.message.reply_text(f"✅ Filter added — I'll reply whenever someone says \"{trigger}\".")


async def delfilters_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    if not context.args:
        await update.message.reply_text("⚠️ Use: /delfilters <trigger>")
        return
    trigger = context.args[0].lower()
    chat_id = update.effective_chat.id
    if FILTERS.get(chat_id, {}).pop(trigger, None) is not None:
        await update.message.reply_text(f"🗑️ Filter \"{trigger}\" removed.")
    else:
        await update.message.reply_text(f"⚠️ No filter found for \"{trigger}\".")


# ---------------------------------------------------------------------------
# BLOCKLIST (banned words)
# ---------------------------------------------------------------------------
async def addblocklist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    if not context.args:
        await update.message.reply_text("⚠️ Use: /addblocklist <word1> <word2> ...")
        return
    chat_id = update.effective_chat.id
    words = {w.lower() for w in context.args}
    BLOCKLIST.setdefault(chat_id, set()).update(words)
    await update.message.reply_text(f"🚫 Added to blocklist: {', '.join(sorted(words))}")


async def removeblock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    if not context.args:
        await update.message.reply_text("⚠️ Use: /removeblock <word1> <word2> ...")
        return
    chat_id = update.effective_chat.id
    removed = []
    for w in context.args:
        w = w.lower()
        if w in BLOCKLIST.get(chat_id, set()):
            BLOCKLIST[chat_id].discard(w)
            removed.append(w)
    if removed:
        await update.message.reply_text(f"♻️ Removed from blocklist: {', '.join(removed)}")
    else:
        await update.message.reply_text("⚠️ None of those words were in the blocklist.")


# ---------------------------------------------------------------------------
# WELCOME MESSAGE
# ---------------------------------------------------------------------------
async def setwelcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    if not context.args:
        await update.message.reply_text("⚠️ Use: /setwelcome <message> — use {name} for the new member's name.")
        return
    WELCOME[update.effective_chat.id] = " ".join(context.args)
    await update.message.reply_text("✅ Welcome message has been set.")


async def delsetwelcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    chat_id = update.effective_chat.id
    if WELCOME.pop(chat_id, None) is not None:
        await update.message.reply_text("🗑️ Welcome message removed.")
    else:
        await update.message.reply_text("⚠️ No welcome message was set.")


async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    template = WELCOME.get(chat_id)
    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            continue  # don't welcome the bot itself
        text = template.replace("{name}", member.full_name) if template else f"🎉 Welcome to the group, {mention(member)}!"
        await update.message.reply_text(text)


# ---------------------------------------------------------------------------
# TRUTH / DARE
# ---------------------------------------------------------------------------
async def truth_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = random.choice(TRUTH_QUESTIONS)
    await update.message.reply_text(f"🤔 *Truth:*\n{q}", parse_mode="Markdown")


async def dare_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = random.choice(DARE_CHALLENGES)
    await update.message.reply_text(f"🔥 *Dare:*\n{d}", parse_mode="Markdown")


# ---------------------------------------------------------------------------
# TRANSLATE
# ---------------------------------------------------------------------------
async def translate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Use: /tr <language> (reply to a message) or /tr <language> <text>")
        return
    target_lang = context.args[0]

    if update.message.reply_to_message and update.message.reply_to_message.text:
        source_text = update.message.reply_to_message.text
    elif len(context.args) > 1:
        source_text = " ".join(context.args[1:])
    else:
        await update.message.reply_text("⚠️ Reply to a message to translate, or add text after the language.")
        return

    if not gemini_client:
        await update.message.reply_text("⚠️ Translation needs GEMINI_API_KEY to be set.")
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
        await update.message.reply_text(f"🌐 *Translation ({target_lang}):*\n{translated}", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Translate error: {e}")
        await update.message.reply_text("❌ Couldn't translate right now, try again shortly.")


# ---------------------------------------------------------------------------
# WORD CHAIN GAME (turn-based, multiplayer, with elimination)
# ---------------------------------------------------------------------------
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
        f"⏰ {mention(eliminated)} ran out of time and is *eliminated*!",
        parse_mode="Markdown",
    )

    if len(players) <= 1:
        if players:
            await context.bot.send_message(
                chat_id, f"🏆 {mention(players[0])} wins the Word Chain game! 🎉", parse_mode="Markdown"
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
        parse_mode="Markdown",
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
            "🔗 *Word Chain Game — Lobby Open!*\n\n"
            "Type /join to enter.\n"
            "Once at least 2 players have joined, send /game again to start!",
            parse_mode="Markdown",
        )
        return

    if state["phase"] == "lobby":
        if len(state["players"]) < 2:
            await update.message.reply_text("⚠️ Need at least 2 players to start! Use /join to join the lobby.")
            return
        state["phase"] = "playing"
        random.shuffle(state["players"])
        state["current_index"] = 0
        current = state["players"][0]
        await update.message.reply_text(
            f"🚀 *Game Started!* {len(state['players'])} players are in.\n\n"
            f"📋 Rules: each word must start with the last letter of the previous word, "
            f"no repeats, and it gets harder every few rounds (less time, longer words).\n\n"
            f"🎯 First turn: {mention(current)} — send any word within {state['time_limit']}s!",
            parse_mode="Markdown",
        )
        await schedule_game_timeout(context, chat_id)
        return

    # phase == "playing" -> a second /game call ends it
    job = state.get("job")
    if job:
        job.schedule_removal()
    GAME_STATE.pop(chat_id, None)
    await update.message.reply_text(f"🛑 Word Chain game ended at round {state['round']}.")


async def join_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = GAME_STATE.get(chat_id)
    if not state or state["phase"] != "lobby":
        await update.message.reply_text("⚠️ No open lobby right now. Use /game to start one!")
        return
    user = update.effective_user
    if any(p["id"] == user.id for p in state["players"]):
        await update.message.reply_text("⚠️ You've already joined the lobby!")
        return
    player = {"id": user.id, "full_name": user.full_name, "username": user.username}
    state["players"].append(player)
    await update.message.reply_text(f"✅ {mention(player)} joined! ({len(state['players'])} players in lobby)", parse_mode="Markdown")


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
        await update.message.reply_text(f"⚠️ \"{text}\" was already used! Try a different word.")
        return True

    if len(text) < state["min_length"]:
        await update.message.reply_text(f"⚠️ Word must be at least {state['min_length']} letters long!")
        return True

    if state["last_word"] and text[0] != state["last_word"][-1]:
        await update.message.reply_text(f"⚠️ Word must start with \"{state['last_word'][-1].upper()}\"!")
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
        parse_mode="Markdown",
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
        await update.message.reply_text("⚠️ Chat feature won't work — GEMINI_API_KEY is not set.")
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
        await update.message.reply_text("❌ Can't reply right now, please try again in a bit.")


# ---------------------------------------------------------------------------
# STICKER ECHO
# ---------------------------------------------------------------------------
async def sticker_echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.sticker:
        return
    try:
        await update.message.reply_sticker(update.message.sticker.file_id)
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

    if await handle_game_turn(update, context):
        return

    lowered = text.lower()
    for word in BLOCKLIST.get(chat_id, set()):
        if word in lowered:
            try:
                await update.message.delete()
            except Exception as e:
                logger.warning(f"Blocklist delete failed: {e}")
            await context.bot.send_message(chat_id, "🚫 A message was removed for containing a blocked word.")
            return

    for trigger, response in FILTERS.get(chat_id, {}).items():
        if trigger in lowered:
            await update.message.reply_text(response)
            return

    await chat_with_ai(update, context)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
BOT_COMMANDS = [
    BotCommand("start", "👋 Show welcome message and buttons"),
    BotCommand("commands", "📋 List all bot commands"),
    BotCommand("info", "👤 View user info"),
    BotCommand("developer", "👨‍💻 Meet the bot's developer"),
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
    BotCommand("addblocklist", "🚫 Block words in the group"),
    BotCommand("removeblock", "♻️ Unblock words"),
    BotCommand("setwelcome", "💬 Set the welcome message"),
    BotCommand("delsetwelcome", "🗑️ Remove the welcome message"),
    BotCommand("truth", "🤔 Get a random truth question"),
    BotCommand("dare", "🔥 Get a random dare challenge"),
    BotCommand("tr", "🌐 Translate a message"),
    BotCommand("game", "🔗 Open/start the word chain game"),
    BotCommand("join", "🙋 Join the word chain lobby"),
]


async def post_init(app: Application):
    await app.bot.set_my_commands(BOT_COMMANDS)


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
    app.add_handler(CommandHandler("developer", developer))

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

    app.add_handler(CommandHandler("addblocklist", addblocklist_cmd))
    app.add_handler(CommandHandler("removeblock", removeblock_cmd))

    app.add_handler(CommandHandler("setwelcome", setwelcome_cmd))
    app.add_handler(CommandHandler("delsetwelcome", delsetwelcome_cmd))

    app.add_handler(CommandHandler("truth", truth_cmd))
    app.add_handler(CommandHandler("dare", dare_cmd))
    app.add_handler(CommandHandler("tr", translate_cmd))
    app.add_handler(CommandHandler("game", game_cmd))
    app.add_handler(CommandHandler("join", join_cmd))

    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))
    app.add_handler(MessageHandler(filters.Sticker.ALL, sticker_echo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_group_text))

    print("🤖 Bot chalu ho gaya...")
    app.run_polling()


if __name__ == "__main__":
    main()
