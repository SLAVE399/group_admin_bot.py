"""
Telegram Group Admin Bot + AI Chatbot
--------------------------------------
Admin Commands: /kick /ban /unban /mute /unmute /warn /warnings /resetwarns
                /promote /demote /pin /unpin /purge /info /rules /setrules

Chat Feature: Bot replies using Google Gemini (free API) when:
  - Message is in a private chat (DM), OR
  - Bot is @mentioned in a group, OR
  - Someone replies to the bot's own message in a group

Setup:
1. pip install -r requirements.txt
2. Set TELEGRAM_BOT_TOKEN env var (bot token from BotFather)
3. Set GEMINI_API_KEY env var (free, from aistudio.google.com/apikey)
4. Add the bot to your group and make it an ADMIN with these rights:
   - Ban users, Delete messages, Pin messages, Add new admins (for /promote)
5. Run: python group_admin_bot.py

Usage notes:
- Most admin commands work by REPLYING to the target user's message.
  Example: reply to someone's message with "/ban" or "/ban spamming"
- /mute [minutes] -> mutes for given minutes (default: forever until /unmute)
- Only group admins/owner can use admin commands (checked live via Telegram API)
"""

import logging
import os
from datetime import timedelta

from google import genai
from telegram import Update, ChatPermissions
from telegram.constants import ChatMemberStatus, ChatType
from telegram.ext import (
    Application,
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

# short in-memory conversation history per chat: {chat_id: [{"role":..,"parts":[..]}, ...]}
CHAT_HISTORY: dict[int, list] = {}
MAX_HISTORY_MESSAGES = 10  # keep last N messages (user+model) per chat

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# in-memory warning store: {chat_id: {user_id: count}}
WARNINGS: dict[int, dict[int, int]] = {}
MAX_WARNINGS = 3

# in-memory rules store: {chat_id: "rules text"}
RULES: dict[int, str] = {}


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """Check if a user is admin/owner in the current chat."""
    try:
        member = await context.bot.get_chat_member(update.effective_chat.id, user_id)
        return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception as e:
        logger.warning(f"is_admin check failed: {e}")
        return False


async def require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Ensures command sender is an admin. Sends a warning message if not."""
    user = update.effective_user
    if not await is_admin(update, context, user.id):
        await update.message.reply_text("❌ Only group admins can use this command.")
        return False
    return True


def get_target_user(update: Update):
    """Returns the user object of the message being replied to, else None."""
    if update.message.reply_to_message:
        return update.message.reply_to_message.from_user
    return None


async def ensure_bot_is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    bot_member = await context.bot.get_chat_member(update.effective_chat.id, context.bot.id)
    if bot_member.status != ChatMemberStatus.ADMINISTRATOR:
        await update.message.reply_text(
            "⚠️ Please make me an ADMIN first (with ban/mute/pin permissions), then this command will work."
        )
        return False
    return True


# ---------------------------------------------------------------------------
# BASIC COMMANDS
# ---------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hello! I'm a group management bot.\n\n"
        "Make me an ADMIN, then REPLY to a user's message "
        "to use these commands:\n\n"
        "/kick - remove user from group (can rejoin)\n"
        "/ban - permanently ban user\n"
        "/unban <user_id> - unban a user\n"
        "/mute [minutes] - mute user\n"
        "/unmute - unmute user\n"
        "/warn [reason] - warn user\n"
        "/warnings - check user's warnings\n"
        "/resetwarns - reset user's warnings\n"
        "/promote - make user an admin\n"
        "/demote - remove user's admin rights\n"
        "/pin - pin the replied message\n"
        "/unpin - remove pinned message\n"
        "/purge - delete messages from reply point onward\n"
        "/rules - view group rules\n"
        "/setrules <text> - set group rules (admin only)\n"
        "/info - view your own or another user's info"
    )


async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = get_target_user(update) or update.effective_user
    admin_status = await is_admin(update, context, target.id)
    text = (
        f"👤 Name: {target.full_name}\n"
        f"🆔 ID: {target.id}\n"
        f"🔗 Username: @{target.username if target.username else 'N/A'}\n"
        f"🛡️ Admin: {'Yes' if admin_status else 'No'}"
    )
    await update.message.reply_text(text)


# ---------------------------------------------------------------------------
# KICK / BAN / UNBAN
# ---------------------------------------------------------------------------
async def kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    target = get_target_user(update)
    if not target:
        await update.message.reply_text("⚠️ Reply to the user's message you want to kick with /kick.")
        return
    chat_id = update.effective_chat.id
    try:
        await context.bot.ban_chat_member(chat_id, target.id)
        await context.bot.unban_chat_member(chat_id, target.id)  # unban so they can rejoin
        await update.message.reply_text(f"👢 {target.full_name} has been kicked from the group.")
    except Exception as e:
        await update.message.reply_text(f"❌ Kick failed: {e}")


async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    target = get_target_user(update)
    if not target:
        await update.message.reply_text("⚠️ Reply to the user's message you want to ban with /ban.")
        return
    reason = " ".join(context.args) if context.args else "No reason given"
    chat_id = update.effective_chat.id
    try:
        await context.bot.ban_chat_member(chat_id, target.id)
        await update.message.reply_text(f"🔨 {target.full_name} has been banned.\nReason: {reason}")
    except Exception as e:
        await update.message.reply_text(f"❌ Ban failed: {e}")


async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    if not context.args:
        await update.message.reply_text("⚠️ Use: /unban <user_id>")
        return
    try:
        user_id = int(context.args[0])
        await context.bot.unban_chat_member(update.effective_chat.id, user_id, only_if_banned=True)
        await update.message.reply_text(f"✅ User {user_id} has been unbanned.")
    except Exception as e:
        await update.message.reply_text(f"❌ Unban failed: {e}")


# ---------------------------------------------------------------------------
# MUTE / UNMUTE
# ---------------------------------------------------------------------------
async def mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    target = get_target_user(update)
    if not target:
        await update.message.reply_text("⚠️ Reply to the user's message you want to mute with /mute.")
        return

    minutes = None
    if context.args:
        try:
            minutes = int(context.args[0])
        except ValueError:
            await update.message.reply_text("⚠️ Enter minutes as a number. Example: /mute 30")
            return

    permissions = ChatPermissions(
        can_send_messages=False,
        can_send_audios=False,
        can_send_documents=False,
        can_send_photos=False,
        can_send_videos=False,
        can_send_video_notes=False,
        can_send_voice_notes=False,
        can_send_polls=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
    )

    until_date = None
    if minutes:
        until_date = update.message.date + timedelta(minutes=minutes)

    try:
        await context.bot.restrict_chat_member(
            update.effective_chat.id, target.id, permissions=permissions, until_date=until_date
        )
        duration_text = f" for {minutes} minutes" if minutes else " (until unmuted)"
        await update.message.reply_text(f"🔇 {target.full_name} has been muted{duration_text}.")
    except Exception as e:
        await update.message.reply_text(f"❌ Mute failed: {e}")


async def unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    target = get_target_user(update)
    if not target:
        await update.message.reply_text("⚠️ Reply to the user's message you want to unmute with /unmute.")
        return

    permissions = ChatPermissions(
        can_send_messages=True,
        can_send_audios=True,
        can_send_documents=True,
        can_send_photos=True,
        can_send_videos=True,
        can_send_video_notes=True,
        can_send_voice_notes=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
    )
    try:
        await context.bot.restrict_chat_member(update.effective_chat.id, target.id, permissions=permissions)
        await update.message.reply_text(f"🔊 {target.full_name} has been unmuted.")
    except Exception as e:
        await update.message.reply_text(f"❌ Unmute failed: {e}")


# ---------------------------------------------------------------------------
# WARNINGS
# ---------------------------------------------------------------------------
async def warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    target = get_target_user(update)
    if not target:
        await update.message.reply_text("⚠️ Reply to the user's message you want to warn with /warn.")
        return

    reason = " ".join(context.args) if context.args else "No reason given"
    chat_id = update.effective_chat.id
    WARNINGS.setdefault(chat_id, {})
    WARNINGS[chat_id][target.id] = WARNINGS[chat_id].get(target.id, 0) + 1
    count = WARNINGS[chat_id][target.id]

    await update.message.reply_text(
        f"⚠️ {target.full_name} has been warned. ({count}/{MAX_WARNINGS})\nReason: {reason}"
    )

    if count >= MAX_WARNINGS:
        try:
            await context.bot.ban_chat_member(chat_id, target.id)
            WARNINGS[chat_id][target.id] = 0
            await update.message.reply_text(
                f"🔨 {target.full_name} reached {MAX_WARNINGS} warnings and has been banned."
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Auto-ban failed: {e}")


async def warnings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = get_target_user(update) or update.effective_user
    chat_id = update.effective_chat.id
    count = WARNINGS.get(chat_id, {}).get(target.id, 0)
    await update.message.reply_text(f"⚠️ {target.full_name}'s warnings: {count}/{MAX_WARNINGS}")


async def resetwarns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    target = get_target_user(update)
    if not target:
        await update.message.reply_text("⚠️ Reply to the user's message whose warnings you want to reset with /resetwarns.")
        return
    chat_id = update.effective_chat.id
    WARNINGS.setdefault(chat_id, {})[target.id] = 0
    await update.message.reply_text(f"✅ {target.full_name}'s warnings have been reset.")


# ---------------------------------------------------------------------------
# PROMOTE / DEMOTE
# ---------------------------------------------------------------------------
async def promote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    target = get_target_user(update)
    if not target:
        await update.message.reply_text("⚠️ Reply to the user's message you want to promote with /promote.")
        return
    try:
        await context.bot.promote_chat_member(
            update.effective_chat.id,
            target.id,
            can_change_info=True,
            can_delete_messages=True,
            can_invite_users=True,
            can_restrict_members=True,
            can_pin_messages=True,
            can_promote_members=False,
        )
        await update.message.reply_text(f"⬆️ {target.full_name} has been made an admin.")
    except Exception as e:
        await update.message.reply_text(f"❌ Promote failed: {e}")


async def demote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    target = get_target_user(update)
    if not target:
        await update.message.reply_text("⚠️ Reply to the user's message you want to demote with /demote.")
        return
    try:
        await context.bot.promote_chat_member(
            update.effective_chat.id,
            target.id,
            can_change_info=False,
            can_delete_messages=False,
            can_invite_users=False,
            can_restrict_members=False,
            can_pin_messages=False,
            can_promote_members=False,
        )
        await update.message.reply_text(f"⬇️ {target.full_name}'s admin rights have been removed.")
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
        await context.bot.pin_chat_message(
            update.effective_chat.id, update.message.reply_to_message.message_id
        )
        await update.message.reply_text("📌 Message pinned.")
    except Exception as e:
        await update.message.reply_text(f"❌ Pin failed: {e}")


async def unpin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context) or not await ensure_bot_is_admin(update, context):
        return
    try:
        await context.bot.unpin_chat_message(update.effective_chat.id)
        await update.message.reply_text("📌 Message unpinned.")
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
            pass  # message might already be deleted / too old

    info_msg = await context.bot.send_message(chat_id, f"🧹 {deleted} messages deleted.")


# ---------------------------------------------------------------------------
# RULES
# ---------------------------------------------------------------------------
async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = RULES.get(chat_id, "No rules have been set for this group yet.")
    await update.message.reply_text(f"📜 Group Rules:\n\n{text}")


async def setrules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    if not context.args:
        await update.message.reply_text("⚠️ Use: /setrules <rules text>")
        return
    RULES[update.effective_chat.id] = " ".join(context.args)
    await update.message.reply_text("✅ Rules have been set.")


# ---------------------------------------------------------------------------
# AI CHAT (Claude)
# ---------------------------------------------------------------------------
async def should_respond_as_chatbot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    message = update.message

    if chat.type == ChatType.PRIVATE:
        return True

    # In groups: only respond if bot is @mentioned or the message replies to the bot
    bot_username = context.bot.username
    if message.reply_to_message and message.reply_to_message.from_user.id == context.bot.id:
        return True
    if bot_username and message.text and f"@{bot_username}" in message.text:
        return True
    return False


async def chat_with_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    if update.message.text.startswith("/"):
        return  # let CommandHandlers deal with commands
    if not await should_respond_as_chatbot(update, context):
        return
    if not gemini_client:
        await update.message.reply_text(
            "⚠️ Chat feature won't work — GEMINI_API_KEY is not set."
        )
        return

    chat_id = update.effective_chat.id
    user_text = update.message.text.replace(f"@{context.bot.username}", "").strip()

    history = CHAT_HISTORY.setdefault(chat_id, [])
    history.append({"role": "user", "parts": [{"text": user_text}]})
    history[:] = history[-MAX_HISTORY_MESSAGES:]  # trim old messages

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
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
# MAIN
# ---------------------------------------------------------------------------
def main():
    if BOT_TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":
        print("⚠️  Pehle BOT_TOKEN set karo (script me ya TELEGRAM_BOT_TOKEN env var me)!")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("info", info))

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

    # AI chatbot for normal (non-command) text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_with_ai))

    print("🤖 Bot chalu ho gaya...")
    app.run_polling()


if __name__ == "__main__":
    main()
