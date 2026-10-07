"""
Telegram Frame Bot
------------------
ទទួលរូបភាព (១សន្លឹក ឬ Album) ហើយដាក់ frame.png ជុំវិញ
មុននឹងផ្ញើត្រឡប់ទៅអ្នកប្រើប្រាស់វិញ។
"""

import asyncio
import io
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, HTTPServer

from dotenv import load_dotenv
from PIL import Image, ImageOps
from telegram import InputMediaPhoto, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Load .env file (locally) — ignored if vars already set (e.g. Render)
load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not TOKEN:
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN មិនត្រូវបានដាក់ក្នុង Environment Variables ទេ។"
    )

FRAME_PATH: str = os.environ.get("FRAME_PATH", "frame.png")
ALBUM_TIMEOUT: float = float(os.environ.get("ALBUM_TIMEOUT", "2.0"))
JPEG_QUALITY: int = int(os.environ.get("JPEG_QUALITY", "90"))

# Thread pool សម្រាប់ CPU-bound image processing (PIL blocks the event loop)
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="img_worker")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keep-alive HTTP server (Render / Railway)
# ---------------------------------------------------------------------------


class _SilentHandler(BaseHTTPRequestHandler):
    """HTTP handler ដែលមិន spam log នៅ console។"""

    def do_GET(self) -> None:  # noqa: N802
        body = b"Bot is running"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_) -> None:
        pass  # suppress per-request stdout noise


def _run_keepalive_server() -> None:
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), _SilentHandler)
    logger.info("Keep-alive server listening on port %d", port)
    server.serve_forever()


# ---------------------------------------------------------------------------
# Frame cache — load frame.png ១ ដង រក្សាទុក Memory
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _get_frame() -> Image.Image:
    """Load និង cache frame image។ Raise FileNotFoundError បើរកមិនឃើញ។"""
    logger.info("Loading frame from: %s", FRAME_PATH)
    return Image.open(FRAME_PATH).convert("RGBA")


# ---------------------------------------------------------------------------
# Image processing — runs in thread pool (non-blocking to event loop)
# ---------------------------------------------------------------------------


def _apply_frame_sync(image_bytes: bytes) -> bytes:
    """
    CPU-bound — ត្រូវ run ក្នុង executor ជានិច្ច។
    យក raw image bytes, ដាក់ frame, return JPEG bytes។
    """
    frame = _get_frame()
    user_img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    user_img = ImageOps.fit(user_img, frame.size, method=Image.Resampling.LANCZOS)
    combined = Image.alpha_composite(user_img, frame)
    out = io.BytesIO()
    combined.convert("RGB").save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue()


async def _apply_frame(image_bytes: bytes) -> bytes:
    """Async wrapper — offload PIL processing ទៅ thread pool។"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, _apply_frame_sync, image_bytes)


# ---------------------------------------------------------------------------
# Album collector — thread-safe ដោយប្រើ asyncio.Lock
# ---------------------------------------------------------------------------


class _AlbumCollector:
    """
    ប្រមូលរូបភាពក្នុង media group ដូចគ្នា ហើយ process នៅពេលផុត timeout។
    ប្រើ asyncio.Lock ដើម្បីការពារ race condition ពេល concurrent updates។
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._albums: dict[str, list] = {}

    async def add(self, group_id: str, photo, on_complete) -> None:
        async with self._lock:
            is_new = group_id not in self._albums
            if is_new:
                self._albums[group_id] = []
            self._albums[group_id].append(photo)

        if is_new:
            # Timer task — wait then flush (runs outside lock to avoid deadlock)
            asyncio.create_task(self._wait_and_flush(group_id, on_complete))

    async def _wait_and_flush(self, group_id: str, on_complete) -> None:
        await asyncio.sleep(ALBUM_TIMEOUT)
        async with self._lock:
            photos = self._albums.pop(group_id, [])
        if photos:
            await on_complete(photos)


_album_collector = _AlbumCollector()

# ---------------------------------------------------------------------------
# Telegram handlers
# ---------------------------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "ជម្រាបសួរ! 👋\n"
        "សូមផ្ញើរូបភាព (១សន្លឹក ឬច្រើនសន្លឹកព្រមគ្នា)\n"
        "ខ្ញុំនឹងបំពាក់ Frame/ស៊ុម ជូនអ្នកវិញ។ 🖼️"
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    media_group_id = message.media_group_id

    # ── Single photo ──────────────────────────────────────────────────────
    if not media_group_id:
        status = await message.reply_text("⏳ កំពុងដំណើរការដាក់ Frame...")
        try:
            photo_file = await message.photo[-1].get_file()
            raw = bytes(await photo_file.download_as_bytearray())
            result = await _apply_frame(raw)
            await message.reply_photo(
                photo=io.BytesIO(result),
                caption="✅ នេះជារូបភាពរបស់អ្នក!",
            )
        except FileNotFoundError:
            await status.edit_text("❌ Error: រកមិនឃើញ File 'frame.png' នៅ Server ទេ។")
            return
        except Exception as exc:
            logger.exception("Single photo processing error")
            await status.edit_text(f"❌ មានបញ្ហាក្នុងការដំណើរការ: {exc}")
            return
        finally:
            try:
                await status.delete()
            except Exception:
                pass
        return

    # ── Album photo ───────────────────────────────────────────────────────
    async def process_album(photos: list) -> None:
        """Callback ត្រូវបាន call ពេល album timeout ផុត។"""
        status = await message.reply_text(
            f"⏳ ដំណើរការ {len(photos)} រូបភាព... សូមរង់ចាំ"
        )
        try:
            # Download រូបភាពទាំងអស់ parallel
            async def _download(photo_obj) -> bytes:
                f = await photo_obj.get_file()
                return bytes(await f.download_as_bytearray())

            raw_list = await asyncio.gather(*[_download(p) for p in photos])

            # Apply frame parallel ក្នុង thread pool
            framed_list = await asyncio.gather(*[_apply_frame(r) for r in raw_list])

            media = [InputMediaPhoto(media=io.BytesIO(data)) for data in framed_list]
            await message.reply_media_group(media=media)

        except FileNotFoundError:
            await status.edit_text("❌ Error: រកមិនឃើញ File 'frame.png' នៅ Server ទេ។")
            return
        except Exception as exc:
            logger.exception("Album processing error")
            await status.edit_text(f"❌ មានបញ្ហាក្នុងការផ្ញើរូបភាព: {exc}")
            return
        finally:
            try:
                await status.delete()
            except Exception:
                pass

    await _album_collector.add(
        group_id=media_group_id,
        photo=message.photo[-1],
        on_complete=process_album,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    # Keep-alive server (daemon thread)
    threading.Thread(target=_run_keepalive_server, daemon=True).start()

    # Pre-load frame ដើម្បីចាប់ Error ពីដំបូង មុន bot run
    try:
        _get_frame()
        logger.info("frame.png loaded successfully.")
    except FileNotFoundError:
        logger.critical(
            "frame.png not found at '%s' — bot will reject all images!", FRAME_PATH
        )

    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .concurrent_updates(True)  # handle updates ច្រើន parallel
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo, block=False))

    logger.info("Bot is starting with polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
