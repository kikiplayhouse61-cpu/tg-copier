import os
import json
import asyncio
import pickle
from telethon import TelegramClient, errors
from telethon.tl.types import MessageMediaWebPage

MEDIA_DIR  = "downloads"
CACHE_FILE = "msg_cache.pkl"
STATE_FILE = "state.json"
RETRIES    = 5

os.makedirs(MEDIA_DIR, exist_ok=True)


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {}

def save_state(s):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, indent=2)
    os.replace(tmp, STATE_FILE)

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "rb") as f:
            return pickle.load(f)
    return {}

def save_cache(c):
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "wb") as f:
        pickle.dump(c, f)
    os.replace(tmp, CACHE_FILE)


async def download_with_retry(client, msg, status_cb=None):
    for attempt in range(1, RETRIES + 1):
        try:
            path = await client.download_media(msg, file=MEDIA_DIR + "/")
            if path:
                return path
        except errors.FloodWaitError as e:
            if status_cb:
                await status_cb(f"⏳ FloodWait: {e.seconds}s...")
            await asyncio.sleep(e.seconds + 2)
        except Exception:
            if attempt < RETRIES:
                await asyncio.sleep(attempt * 3)
    return None


async def send_one(client, msg, dest_entity, status_cb=None):
    text  = msg.raw_text or ""
    media = msg.media

    try:
        if not media or isinstance(media, MessageMediaWebPage):
            if text:
                await client.send_message(
                    dest_entity, text,
                    formatting_entities=msg.entities,
                    link_preview=isinstance(media, MessageMediaWebPage),
                )
        else:
            path = await download_with_retry(client, msg, status_cb)
            if not path:
                return False
            try:
                await client.send_file(
                    dest_entity, path,
                    caption=text or None,
                    formatting_entities=msg.entities,
                )
            finally:
                try:
                    os.remove(path)
                except:
                    pass
        return True

    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds + 2)
        return False
    except Exception as e:
        if status_cb:
            await status_cb(f"⚠️ Send error: {e}")
        return False


async def cache_source(client, source_id, cache_key, status_cb=None):
    cache     = load_cache()
    msg_store = cache.get(cache_key, {})
    last_id   = max(msg_store.keys()) if msg_store else 0

    if status_cb:
        await status_cb(
            f"📥 *Caching shuru*\n"
            f"Already cached: `{len(msg_store)}`\n"
            f"Resume ID: `{last_id}`"
        )

    new_count = 0
    try:
        # ID se directly access karo
        source_entity = await client.get_entity(source_id)

        async for msg in client.iter_messages(
            source_entity,
            reverse=True,
            min_id=last_id,
            wait_time=2,
        ):
            if not msg or msg.action:
                continue

            msg_store[msg.id] = msg
            new_count += 1

            if new_count % 50 == 0:
                cache[cache_key] = msg_store
                save_cache(cache)
                if status_cb:
                    await status_cb(
                        f"💾 Cached: `{len(msg_store)}` messages\n"
                        f"New this run: `{new_count}`"
                    )

    except errors.ChannelPrivateError:
        if status_cb:
            await status_cb(
                "⚠️ Channel private ho gaya!\n"
                "Jo cache hua woh safe hai."
            )
    except Exception as e:
        if status_cb:
            await status_cb(f"⚠️ Error: {e}")
    finally:
        cache[cache_key] = msg_store
        save_cache(cache)

    if status_cb:
        await status_cb(
            f"✅ *Cache complete!*\n"
            f"Total: `{len(msg_store)}`\n"
            f"New: `{new_count}`"
        )
    return msg_store


async def transfer_from_cache(client, cache_key, dest_id, state, status_cb=None):
    cache     = load_cache()
    msg_store = cache.get(cache_key, {})

    if not msg_store:
        if status_cb:
            await status_cb("❌ Cache empty. Pehle cache karo.")
        return 0, 0

    dest_entity     = await client.get_entity(dest_id)
    done_key        = cache_key + "_done"
    transferred_ids = set(state.get(done_key, []))
    sorted_ids      = sorted(msg_store.keys())
    remaining       = [i for i in sorted_ids if i not in transferred_ids]

    if status_cb:
        await status_cb(
            f"📤 *Transfer shuru*\n"
            f"Total: `{len(sorted_ids)}`\n"
            f"Already sent: `{len(transferred_ids)}`\n"
            f"Remaining: `{len(remaining)}`"
        )

    success = 0
    failed  = 0

    for msg_id in remaining:
        msg = msg_store.get(msg_id)
        if not msg:
            continue

        ok = await send_one(client, msg, dest_entity, status_cb)

        if ok:
            success += 1
            transferred_ids.add(msg_id)
            state[done_key] = list(transferred_ids)
            save_state(state)
        else:
            failed += 1

        if (success + failed) % 50 == 0 and status_cb:
            await status_cb(
                f"📊 Progress:\n"
                f"✅ Sent: `{success}`\n"
                f"❌ Failed: `{failed}`\n"
                f"⏳ Left: `{len(remaining)-success-failed}`"
            )

        await asyncio.sleep(1)

    if status_cb:
        await status_cb(
            f"🎉 *Complete!*\n"
            f"✅ Success: `{success}`\n"
            f"❌ Failed: `{failed}`"
        )
    return success, failed
