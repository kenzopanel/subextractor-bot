import os
import time
import asyncio
from typing import BinaryIO, Optional, Dict
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    filters
)
import logging
from dotenv import load_dotenv
from .subtitle_extractor import SubtitleExtractor
from .video_downloader import VideoDownloader
from .system_stats import SystemStats

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class SubtitleBot:
    # Conversation states
    WAITING_FOR_URL = 1
    
    def __init__(self):
        self.token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.temp_dir = '/usr/src/app/downloads'
        
        # Create temp directory if it doesn't exist
        os.makedirs(self.temp_dir, exist_ok=True)
        
        # Initialize components
        self.video_downloader = VideoDownloader(self.temp_dir)
        self.system_stats = SystemStats()
        
        # Track active downloads: gid -> message_id
        self.active_downloads: Dict[str, int] = {}
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send a message when the command /start is issued."""
        keyboard = [
            [
                InlineKeyboardButton("Upload MKV File", callback_data="upload"),
                InlineKeyboardButton("Download from URL", callback_data="url")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            'Hi! I can help you extract subtitles from MKV videos.\n'
            'Choose how you want to provide the video:',
            reply_markup=reply_markup
        )
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send a message when the command /help is issued."""
        help_text = (
            'I can extract subtitles from MKV videos in two ways:\n\n'
            '1. Upload an MKV file directly to me\n'
            '2. Provide a URL to download an MKV file\n\n'
            'Supported subtitle formats: SRT, ASS, SUP\n\n'
            'Use /start to begin!'
        )
        await update.message.reply_text(help_text)
    
    async def download_video(self, file_id: str, context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
        """Download video file from Telegram"""
        try:
            file = await context.bot.get_file(file_id)
            temp_path = os.path.join(self.temp_dir, f"{file_id}.mkv")
            await file.download_to_drive(temp_path)
            return temp_path
        except Exception as e:
            logger.error(f"Error downloading video: {e}")
            return None
    
    async def handle_video(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle video messages."""
        try:
            # Check if the file is an MKV
            if not update.message.document or not update.message.document.file_name.lower().endswith('.mkv'):
                await update.message.reply_text(
                    'Please send an MKV video file.'
                )
                return
            
            # Send processing message
            status_message = await update.message.reply_text(
                'Processing your video file...'
            )
            
            # Download the video
            video_path = await self.download_video(
                update.message.document.file_id,
                context
            )
            
            if not video_path:
                await status_message.edit_text(
                    'Error: Could not download the video file.'
                )
                return
            
            # Extract subtitles
            extractor = SubtitleExtractor(video_path)
            subtitles = extractor.extract_subtitles()
            
            if not subtitles:
                await status_message.edit_text(
                    'No subtitles found in the video file.'
                )
                return
            
            # Send each subtitle file
            await status_message.edit_text(
                f'Found {len(subtitles)} subtitle tracks. Uploading...'
            )
            
            for sub in subtitles:
                with open(sub['path'], 'rb') as f:
                    filename = f"subtitle_{sub['language']}.{sub['format']}"
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=f,
                        filename=filename,
                        caption=f"Language: {sub['language'].upper()}, Format: {sub['format'].upper()}"
                    )
            
            await status_message.edit_text('All subtitles have been extracted and uploaded!')
            
            # Cleanup
            extractor.cleanup()
            os.remove(video_path)
            
        except Exception as e:
            logger.error(f"Error processing video: {e}")
            await update.message.reply_text(
                'Sorry, an error occurred while processing your video.'
            )
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle button clicks"""
        query = update.callback_query
        await query.answer()
        
        if query.data == "upload":
            await query.edit_message_text(
                "Please send me your MKV video file."
            )
        elif query.data == "url":
            await query.edit_message_text(
                "Please send me the URL of the MKV video."
            )
            return self.WAITING_FOR_URL
    
    def format_size(self, size_bytes: float) -> str:
        """Format bytes to human readable string."""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024:
                return f"{size_bytes:.2f}{unit}"
            size_bytes /= 1024
        return f"{size_bytes:.2f}TB"

    def format_progress_bar(self, percentage: float, width: int = 12) -> str:
        """Create a progress bar string."""
        filled = int(width * percentage / 100)
        return f"[{'▧' * filled}{'□' * (width - filled)}]"

    def format_time(self, seconds: int) -> str:
        """Format seconds to MM:SS or HH:MM:SS."""
        if seconds < 0:
            return "∞"
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h}h{m}m{s}s" if h > 0 else f"{m}m{s}s"

    async def update_download_status(self, chat_id: int, message_id: int, gid: str, context: ContextTypes.DEFAULT_TYPE):
        """Update download status message every 10 seconds."""
        try:
            while True:
                download = self.video_downloader.get_download(gid)
                if not download or download.is_complete or download.has_failed:
                    break

                # Get download stats
                progress = download.progress
                downloaded = download.completed_length
                total = download.total_length
                speed = download.download_speed
                eta = download.eta or -1

                # Get system stats
                sys_stats = self.system_stats.get_stats()
                aria_stats = self.video_downloader.get_global_stats()
                dl_speed = int(aria_stats.get('downloadSpeed', 0))
                ul_speed = int(aria_stats.get('uploadSpeed', 0))

                # Format status message
                status = (
                    f"{download.name}\n"
                    f"{self.format_progress_bar(progress)} {progress:.2f}%\n"
                    f"Processed: {self.format_size(downloaded)} of {self.format_size(total)}\n"
                    f"Status: Download | ETA: {self.format_time(eta)}\n"
                    f"Speed: {self.format_size(speed)}/s | Elapsed: {self.format_time(int(time.time() - download.start_time))}\n"
                    f"Engine: {self.video_downloader.get_version()}\n"
                    f"/cancel_{gid}\n\n"
                    f"Bot Stats\n"
                    f"CPU: {sys_stats['cpu']} | F: {sys_stats['disk']}\n"
                    f"RAM: {sys_stats['ram']} | UPTIME: {sys_stats['uptime']}\n"
                    f"DL: {self.format_size(dl_speed)}/s | UL: {self.format_size(ul_speed)}/s"
                )

                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=status
                )

                # Sleep for 10 seconds
                await asyncio.sleep(10)

        except Exception as e:
            logger.error(f"Error updating status: {e}")
        finally:
            # Clean up tracking
            if gid in self.active_downloads:
                del self.active_downloads[gid]

    async def url_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle URL input"""
        url = update.message.text
        
        # Start the download
        gid = self.video_downloader.start_download(url)
        if not gid:
            await update.message.reply_text(
                'Error: Could not start download. Please check the URL.'
            )
            return ConversationHandler.END

        # Send initial status message
        status_message = await update.message.reply_text(
            'Starting download...'
        )
        
        # Track this download
        self.active_downloads[gid] = status_message.message_id
        
        # Start status updater task
        asyncio.create_task(
            self.update_download_status(
                update.effective_chat.id,
                status_message.message_id,
                gid,
                context
            )
        )
        
        # Wait for download to complete
        while True:
            download = self.video_downloader.get_download(gid)
            if not download:
                await status_message.edit_text('Error: Download failed or was cancelled.')
                return ConversationHandler.END
                
            if download.is_complete:
                video_path = os.path.join(self.temp_dir, download.name)
                if os.path.exists(video_path):
                    await self.process_video_file(video_path, update, context, status_message)
                    return ConversationHandler.END
            
            elif download.has_failed:
                await status_message.edit_text('Error: Download failed.')
                return ConversationHandler.END
                
            await asyncio.sleep(1)
            
        return ConversationHandler.END
    
    async def process_video_file(self, video_path: str, update: Update, context: ContextTypes.DEFAULT_TYPE, status_message):
        """Process the video file and extract subtitles"""
        try:
            # Extract subtitles
            extractor = SubtitleExtractor(video_path)
            subtitles = extractor.extract_subtitles()
            
            if not subtitles:
                await status_message.edit_text(
                    'No subtitles found in the video file.'
                )
                return
            
            # Send each subtitle file
            await status_message.edit_text(
                f'Found {len(subtitles)} subtitle tracks. Uploading...'
            )
            
            for sub in subtitles:
                with open(sub['path'], 'rb') as f:
                    filename = f"subtitle_{sub['language']}.{sub['format']}"
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=f,
                        filename=filename,
                        caption=f"Language: {sub['language'].upper()}, Format: {sub['format'].upper()}"
                    )
            
            await status_message.edit_text('All subtitles have been extracted and uploaded!')
            
            # Cleanup
            extractor.cleanup()
            os.remove(video_path)
            
        except Exception as e:
            logger.error(f"Error processing video: {e}")
            await status_message.edit_text(
                'Sorry, an error occurred while processing your video.'
            )
    
    async def handle_video(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle video file uploads"""
        try:
            # Check if the file is an MKV
            if not update.message.document or not update.message.document.file_name.lower().endswith('.mkv'):
                await update.message.reply_text(
                    'Please send an MKV video file.'
                )
                return
            
            # Send processing message
            status_message = await update.message.reply_text(
                'Processing your video file...'
            )
            
            # Download the video
            video_path = await self.download_video(
                update.message.document.file_id,
                context
            )
            
            if not video_path:
                await status_message.edit_text(
                    'Error: Could not download the video file.'
                )
                return
            
            await self.process_video_file(video_path, update, context, status_message)
            
        except Exception as e:
            logger.error(f"Error processing video: {e}")
            await update.message.reply_text(
                'Sorry, an error occurred while processing your video.'
            )
    
    async def handle_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /cancel_<gid> commands"""
        try:
            # Extract GID from command
            command = update.message.text
            if not command.startswith('/cancel_'):
                return
                
            gid = command[8:]  # Remove '/cancel_' prefix
            if not gid:
                return
                
            # Try to cancel the download
            if self.video_downloader.cancel_download(gid):
                await update.message.reply_text('Download cancelled.')
                
                # If we have an active status message, update it
                if gid in self.active_downloads:
                    msg_id = self.active_downloads[gid]
                    try:
                        await context.bot.edit_message_text(
                            chat_id=update.effective_chat.id,
                            message_id=msg_id,
                            text='Download cancelled.'
                        )
                    except Exception:
                        pass
            else:
                await update.message.reply_text('Could not cancel download. It may have already completed or failed.')
                
        except Exception as e:
            logger.error(f"Error in cancel handler: {e}")
            await update.message.reply_text('Error processing cancel command.')

    def run(self):
        """Start the bot."""
        # Create application
        application = Application.builder().token(self.token).build()
        
        # Create conversation handler for URL input
        conv_handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.button_handler)],
            states={
                self.WAITING_FOR_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.url_handler)]
            },
            fallbacks=[CommandHandler("start", self.start)]
        )
        
        # Add handlers
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(conv_handler)
        application.add_handler(MessageHandler(filters.Document.ALL, self.handle_video))
        
        # Add cancel command handler - must match /cancel_<gid> pattern
        application.add_handler(MessageHandler(
            filters.Regex(r'^/cancel_[a-zA-Z0-9]+$'),
            self.handle_cancel
        ))
        
        # Start the bot
        application.run_polling()