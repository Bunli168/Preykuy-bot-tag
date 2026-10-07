import io
import logging
from PIL import Image, ImageOps
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# ដាក់ API Token របស់អ្នកនៅទីនេះ
TOKEN = "8878112538:AAGWqcWMnJPiT8CxMr5wPbQp2vvuTIjprUI"

# កំណត់ Logging ដើម្បីមើល Error
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Function នៅពេលអ្នកប្រើប្រាស់ចុច /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ជម្រាបសួរ! សូមផ្ញើរូបភាពមកកាន់ខ្ញុំ ខ្ញុំនឹងបំពាក់ Frame/ស៊ុម ជូនអ្នកវិញ។"
    )

# Function សម្រាប់ចាត់ចែងរូបភាពដែលអ្នកប្រើប្រាស់ផ្ញើមក
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("កំពុងដំណើរការដាក់ Frame...")

    # 1. ទាញយករូបភាពដែលទើបតែ Upload (យកទំហំធំជាងគេ)
    photo_file = await update.message.photo[-1].get_file()
    image_bytes = await photo_file.download_as_bytearray()

    # 2. បើករូបភាពអ្នកប្រើ និងរូបភាព Frame
    user_img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    
    try:
        frame_img = Image.open("frame.png").convert("RGBA")
    except FileNotFoundError:
        await status_msg.edit_text("Error: រកមិនឃើញ File 'frame.png' នៅក្នុង Server ទេ។")
        return

    # 3. កែតម្រូវទំហំរូបភាពអ្នកប្រើប្រាស់ឱ្យស្មើនឹងទំហំ Frame ដោយរក្សាទម្រង់ដើម (Crop Center)
    user_img = ImageOps.fit(user_img, frame_img.size, method=Image.Resampling.LANCZOS)

    # 4. បញ្ចូលរូបភាពទាំងពីរ (ដាក់ Frame ពីលើរូបភាពអ្នកប្រើ)
    combined_img = Image.alpha_composite(user_img, frame_img)

    # 5. រក្សាទុកក្នុង Memory រួចផ្ញើត្រឡប់ទៅកាន់ Telegram វិញ
    output_stream = io.BytesIO()
    combined_img.convert("RGB").save(output_stream, format="JPEG", quality=95)
    output_stream.seek(0)

    await update.message.reply_photo(photo=output_stream, caption="នេះជារូបភាពរបស់អ្នក!")
    await status_msg.delete()

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    print("Bot កំពុងដំណើរការ...")
    app.run_polling()

if __name__ == '__main__':
    main()