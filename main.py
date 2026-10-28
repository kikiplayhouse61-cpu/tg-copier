import os
import json
import asyncio
import logging
from telethon import TelegramClient
from telethon.sessions import StringSession
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler,
)
from copier import (
    load_state, save_state, load_cache,
    cache_source, transfer_from_cache,
)

logging.basicConfig(level=logging.INFO)

TG_API_ID   = int(os.environ["TG_API_ID"])
TG_API_HASH = os.environ["TG_API_HASH"]
BOT_TOKEN   = os.environ["BOT_TOKEN"]
OWNER_ID    = int(os.environ["OWNER_ID"])

WAIT_OTP    = 1
WAIT_2FA    = 2
PICK_SOURCE = 3
PICK_DEST   = 4
CONFIRM     = 5

user_client = None


def owner_only(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id if update.effective_user else 0
        if uid != OWNER_ID:
            return
        return await func(update, ctx)
    return wrapper


async def get_user_client():
    global user_client
    if user_client is None:
        session_str = os.environ.get("SESSION_STRING", "")
        user_client = TelegramClient(
            StringSession(session_str),
            TG_API_ID,
            TG_API_HASH
        )
    if not user_client.is_connected():
        await user_client.connect()
    return user_client


async def send_status(bot, text):
    try:
        await bot.send_message(
            chat_id=OWNER_ID, text=text, parse_mode="Markdown"
        )
    except Exception as e:
        logging.warning(f"status send fail: {e}")


@owner_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *TG Channel Copier Bot*\n\n"
        "Commands:\n"
        "/copy — Source → Destination setup karo\n"
        "/cache — Source cache karo\n"
        "/transfer — Transfer shuru karo\n"
        "/status — Progress dekho\n"
        "/reset — Reset karo",
        parse_mode="Markdown"
    )


@owner_only
async def cmd_copy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    client = await get_user_client()

    try:
        authorized = await client.is_user_authorized()
    except Exception:
        authorized = False

    if not authorized:
        await update.message.reply_text(
            "❌ Session expired. Railway mein SESSION_STRING check karo."
        )
        return ConversationHandler.END

    await update.message.reply_text("⏳ Channels load ho rahe hain...")

    channels = []
    async for d in client.iter_dialogs():
        if getattr(d.entity, "broadcast", False):
            channels.append(d)

    if not channels:
        await update.message.reply_text("❌ Koi channel nahi mila.")
        return ConversationHandler.END

    ctx.user_data["all_channels"] = channels

    buttons = []
    for i, ch in enumerate(channels[:30]):
        buttons.append([InlineKeyboardButton(
            f"📢 {ch.name[:40]}", callback_data=f"src_{i}"
        )])

    await update.message.reply_text(
        "📥 *SOURCE channel chuno*\n(jahan se copy karna hai):",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown"
    )
    return PICK_SOURCE


async def pick_source(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx      = int(query.data.replace("src_", ""))
    channels = ctx.user_data["all_channels"]
    source   = channels[idx]
    ctx.user_data["source"] = source

    owned = [
        ch for ch in channels
        if getattr(ch.entity, "creator", False)
        or getattr(ch.entity, "admin_rights", None)
    ]

    if not owned:
        await query.edit_message_text("❌ Koi owned/admin channel nahi mila.")
        return ConversationHandler.END

    ctx.user_data["owned"] = owned
    buttons = []
    for i, ch in enumerate(owned[:20]):
        buttons.append([InlineKeyboardButton(
            f"✅ {ch.name[:40]}", callback_data=f"dst_{i}"
        )])

    await query.edit_message_text(
        f"✅ Source: *{source.name}*\n\n"
        f"📤 *DESTINATION chuno*:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown"
    )
    return PICK_DEST


async def pick_dest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx   = int(query.data.replace("dst_", ""))
    owned = ctx.user_data["owned"]
    dest  = owned[idx]
    ctx.user_data["dest"] = dest

    source    = ctx.user_data["source"]
    cache_key = f"{source.id}:{dest.id}"
    ctx.user_data["cache_key"] = cache_key

    state = load_state()
    state.update({
        "last_source_id":   source.id,
        "last_source_name": source.name,
        "last_dest_id":     dest.id,
        "last_dest_name":   dest.name,
        "last_cache_key":   cache_key,
    })
    save_state(state)

    cache    = load_cache()
    cached   = len(cache.get(cache_key, {}))
    done_ids = len(state.get(cache_key + "_done", []))

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 Sirf Cache karo", callback_data="do_cache")],
        [InlineKeyboardButton("📤 Sirf Transfer karo", callback_data="do_transfer")],
        [InlineKeyboardButton("🔄 Full Run (Cache + Transfer)", callback_data="do_full")],
    ])

    await query.edit_message_text(
        f"✅ *Setup complete!*\n\n"
        f"📥 Source: `{source.name}`\n"
        f"📤 Dest: `{dest.name}`\n"
        f"💾 Cached: `{cached}` messages\n"
        f"✅ Already sent: `{done_ids}`\n\n"
        f"Kya karna hai?",
        reply_markup=buttons,
        parse_mode="Markdown"
    )
    return CONFIRM


async def confirm_action(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    action = query.data

    source    = ctx.user_data.get("source")
    dest      = ctx.user_data.get("dest")
    cache_key = ctx.user_data.get("cache_key")

    if not source or not dest:
        await query.edit_message_text("❌ /copy dobara karo.")
        return ConversationHandler.END

    await query.edit_message_text(
        f"⚡ *Shuru ho raha hai...*\n"
        f"Updates yahan aate rahenge.",
        parse_mode="Markdown"
    )

    client = await get_user_client()

    async def status_cb(msg):
        await send_status(ctx.bot, msg)

    state = load_state()

    if action in ("do_cache", "do_full"):
        await cache_source(client, source.entity, cache_key, status_cb)

    if action in ("do_transfer", "do_full"):
        await transfer_from_cache(
            client, cache_key, dest.entity, state, status_cb
        )

    return ConversationHandler.END


@owner_only
async def cmd_cache(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    state     = load_state()
    cache_key = state.get("last_cache_key")
    src_id    = state.get("last_source_id")
    if not cache_key:
        await update.message.reply_text("❌ Pehle /copy se setup karo.")
        return
    client = await get_user_client()
    await update.message.reply_text("📥 Caching shuru...")

    async def status_cb(msg):
        await send_status(ctx.bot, msg)

    try:
        src_entity = await client.get_entity(int(src_id))
        await cache_source(client, src_entity, cache_key, status_cb)
    except Exception as e:
        await send_status(ctx.bot, f"❌ Error: {e}")


@owner_only
async def cmd_transfer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    state     = load_state()
    cache_key = state.get("last_cache_key")
    dst_id    = state.get("last_dest_id")
    if not cache_key:
        await update.message.reply_text("❌ Pehle /copy se setup karo.")
        return
    client = await get_user_client()
    await update.message.reply_text("📤 Transfer shuru...")

    async def status_cb(msg):
        await send_status(ctx.bot, msg)

    try:
        dst_entity = await client.get_entity(int(dst_id))
        await transfer_from_cache(
            client, cache_key, dst_entity, state, status_cb
        )
    except Exception as e:
        await send_status(ctx.bot, f"❌ Error: {e}")


@owner_only
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    state     = load_state()
    cache_key = state.get("last_cache_key", "")
    cache     = load_cache()
    cached    = len(cache.get(cache_key, {}))
    done      = len(state.get(cache_key + "_done", []))

    client = await get_user_client()
    logged = False
    try:
        logged = await client.is_user_authorized()
    except Exception:
        pass

    await update.message.reply_text(
        f"📊 *Status*\n\n"
        f"🔐 Session: `{'✅ Active' if logged else '❌ Expired'}`\n"
        f"📥 Source: `{state.get('last_source_name','—')}`\n"
        f"📤 Dest: `{state.get('last_dest_name','—')}`\n"
        f"💾 Cached: `{cached}`\n"
        f"✅ Transferred: `{done}`\n"
        f"⏳ Remaining: `{cached - done}`",
        parse_mode="Markdown"
    )


@owner_only
async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    for f in ["state.json", "msg_cache.pkl"]:
        try:
            os.remove(f)
        except:
            pass
    await update.message.reply_text("✅ Reset ho gaya.")


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    copy_conv = ConversationHandler(
        entry_points=[CommandHandler("copy", cmd_copy)],
        states={
            PICK_SOURCE: [CallbackQueryHandler(pick_source, pattern="^src_")],
            PICK_DEST:   [CallbackQueryHandler(pick_dest,   pattern="^dst_")],
            CONFIRM:     [CallbackQueryHandler(confirm_action, pattern="^do_")],
        },
        fallbacks=[CommandHandler("copy", cmd_copy)],
    )

    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("status",   cmd_status))
    app.add_handler(CommandHandler("cache",    cmd_cache))
    app.add_handler(CommandHandler("transfer", cmd_transfer))
    app.add_handler(CommandHandler("reset",    cmd_reset))
    app.add_handler(copy_conv)

    logging.info("Bot running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
