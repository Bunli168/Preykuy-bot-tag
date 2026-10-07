import io
import logging
import asyncio
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from PIL import Image, ImageOps
from telegram import Update, InputMediaPhoto
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# ដាក់ API Token របស់អ្នកនៅទីនេះ ឬក្នុង Environment Variables របស់ Render (សុវត្ថិភាពជាង)
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8878112538:AAHLoNwowuadq0awKOBrDcPxHEwqlrJhg44")

# កំណត់ Logging ដើម្បីមើល Error
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Bot is running")

def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server_address = ('0.0.0.0', port)
    httpd = HTTPServer(server_address, DummyHandler)
    httpd.serve_forever()

# Function នៅពេលអ្នកប្រើប្រាស់ចុច /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ជម្រាបសួរ! សូមផ្ញើរូបភាពមកកាន់ខ្ញុំ (១សន្លឹក ឬច្រើនសន្លឹកព្រមគ្នា) ខ្ញុំនឹងបំពាក់ Frame/ស៊ុម ជូនអ្នកវិញ។"
    )

# ឃ្លាំងផ្ទុកទិន្នន័យ Album បណ្ដោះអាសន្ន
ALBUM_CACHE = {}
ALBUM_TIMEOUT = 2.5 # រង់ចាំ 2.5 វិនាទីដើម្បីប្រមូលរូបភាពក្នុង Album តែមួយឲ្យអស់

# Function សម្រាប់ចាត់ចែងរូបភាពដែលអ្នកប្រើប្រាស់ផ្ញើមក
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    media_group_id = message.media_group_id

    # ករណីទី១៖ អ្នកប្រើប្រាស់ផ្ញើរូបភាពតែមួយសន្លឹក (មិនមែន Album)
    if not media_group_id:
        status_msg = await message.reply_text("កំពុងដំណើរការដាក់ Frame...")

        photo_file = await message.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()
        user_img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        
        try:
            frame_img = Image.open("frame.png").convert("RGBA")
        except FileNotFoundError:
            await status_msg.edit_text("Error: រកមិនឃើញ File 'frame.png' នៅក្នុង Server ទេ។")
            return

        user_img = ImageOps.fit(user_img, frame_img.size, method=Image.Resampling.LANCZOS)
        combined_img = Image.alpha_composite(user_img, frame_img)

        output_stream = io.BytesIO()
        combined_img.convert("RGB").save(output_stream, format="JPEG", quality=95)
        output_stream.seek(0)

        await message.reply_photo(photo=output_stream, caption="នេះជារូបភាពរបស់អ្នក!")
        await status_msg.delete()
        return

    # ករណីទី២៖ អ្នកប្រើប្រាស់ផ្ញើរូបភាពច្រើនសន្លឹកព្រមគ្នា (Album)
    if media_group_id not in ALBUM_CACHE:
        ALBUM_CACHE[media_group_id] = []
        is_first_photo = True
    else:
        is_first_photo = False

    # រក្សាទុករូបភាពចូលក្នុងឃ្លាំង
    ALBUM_CACHE[media_group_id].append(message.photo[-1])

    # បើជារូបភាពទី១ ក្នុង Album យើងប្រាប់អ្នកប្រើប្រាស់ និងចាប់ផ្ដើមរាប់ថយក្រោយ
    if is_first_photo:
        status_msg = await message.reply_text(f"ទទួលបានរូបភាព... កំពុងរង់ចាំប្រមូល និងដាក់ Frame លើរូបភាពទាំងអស់...")
        
        # រង់ចាំបន្តិចដើម្បីឲ្យ Telegram ផ្ញើរូបភាពបន្ទាប់ៗក្នុង Album មកឲ្យអស់សិន
        await asyncio.sleep(ALBUM_TIMEOUT)
        
        # ផុតកំណត់រង់ចាំ យកកញ្ចប់រូបភាពទាំងអស់មកដំណើរការ
        photos = ALBUM_CACHE.pop(media_group_id, [])
        if not photos:
            return

        try:
            frame_img = Image.open("frame.png").convert("RGBA")
        except FileNotFoundError:
            await status_msg.edit_text("Error: រកមិនឃើញ File 'frame.png' នៅក្នុង Server ទេ។")
            return

        media_group_reply = []
        
        for photo_obj in photos:
            photo_file = await photo_obj.get_file()
            image_bytes = await photo_file.download_as_bytearray()
            
            user_img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
            user_img = ImageOps.fit(user_img, frame_img.size, method=Image.Resampling.LANCZOS)
            combined_img = Image.alpha_composite(user_img, frame_img)
            
            output_stream = io.BytesIO()
            combined_img.convert("RGB").save(output_stream, format="JPEG", quality=95)
            output_stream.seek(0)
            
            media_group_reply.append(InputMediaPhoto(media=output_stream))

        # ផ្ញើត្រឡប់ទៅវិញជា Album ច្រើនសន្លឹកព្រមគ្នា
        if media_group_reply:
            try:
                await message.reply_media_group(media=media_group_reply)
            except Exception as e:
                await message.reply_text(f"មានបញ្ហាក្នុងការផ្ញើរូបភាព: {e}")
                
        await status_msg.delete()

def main():
    # ចាប់ផ្ដើម Dummy Server ដើម្បីកុំឱ្យ Render បិទ
    threading.Thread(target=run_dummy_server, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    print("Bot កំពុងដំណើរការ...")
    app.run_polling()

if __name__ == '__main__':
    main()