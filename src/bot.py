import os, sys, datetime, logging, asyncio
import time
from typing import Optional, Dict
from telegram import Update, Message, InlineKeyboardButton, InlineKeyboardMarkup, error as tg_error
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    filters
)
from io import StringIO
from dotenv import load_dotenv
from .subtitle_extractor import SubtitleExtractor
from .video_downloader import VideoDownloader
from .system_stats import SystemStats

load_dotenv()

log_buffer = StringIO()
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setLevel(logging.INFO)

buffer_handler = logging.StreamHandler(log_buffer)
buffer_handler.setLevel(logging.INFO)

formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
stdout_handler.setFormatter(formatter)
buffer_handler.setFormatter(formatter)

logger.addHandler(stdout_handler)
logger.addHandler(buffer_handler)

logging.getLogger("httpx").setLevel(logging.WARNING)

class DownloadStatus:
    DOWNLOADING = "DOWNLOADING"
    EXTRACTING = "EXTRACTING"
    UPLOADING = "UPLOADING"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"
    CANCELED = "CANCELED"

class SubtitleBot:
    UPDATE_INTERVAL = 10
    STATUS_PAGE_SIZE = int(os.getenv('TASK_SHOW_LIMIT', '4'))
    LOG_LINES = 50  # Number of log lines to show
    
    def __init__(self):
        self.token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.download_dir = os.getenv('DOWNLOAD_DIR', '/tmp/downloads')
        os.makedirs(self.download_dir, exist_ok=True)
        
        self.video_downloader = VideoDownloader(self.download_dir)
        self.system_stats = SystemStats()
        
        # Track all active downloads and messages
        self.active_downloads: Dict[str, Dict] = {}  # gid -> {message_id, chat_id}
        self.status_message_id = None  # ID of the combined status message
        self.status_chat_id = None    # Chat ID where status message is posted
        self.current_page = 0         # Current page for status pagination
        self.latest_command_message = None  # Store the latest command message to reply to
        self.is_canceling = False     # Flag to indicate cancellation in progress
        
        # Progress tracking
        self.extraction_progress: Dict[str, float] = {}  # gid -> progress percentage
        self.upload_progress: Dict[str, float] = {}      # gid -> progress percentage
        self.extraction_speed: Dict[str, int] = {}       # gid -> bytes per second
        self.upload_speed: Dict[str, int] = {}          # gid -> bytes per second
        self.start_times: Dict[str, float] = {}         # gid -> start timestamp
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send a message when the command /start is issued."""
        welcome_text = (
            "*Welcome to Subtitle Extractor Bot*\n\n"
            "I can help you extract subtitles from MKV video files in various formats "
            "(SRT, ASS, SUP) with proper language tags.\n\n"
            "*Key Features:*\n"
            "• Extract subtitles from MKV files\n"
            "• Support multiple subtitle formats\n"
            "• Language tag identification (eng, spa, ind, etc.)\n"
            "• Direct file upload or URL download\n"
            "• Real-time download progress\n\n"
            "Use /help to see available commands and how to use them."
        )
        
        await update.message.reply_text(
            welcome_text,
            parse_mode='Markdown',
            reply_to_message_id=update.message.message_id
        )
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send a message when the command /help is issued."""
        help_text = (
            "*Available Commands*\n\n"
            "1\\. `/extract` or `/e` `{url}`\n"
            "Extract subtitles from video at URL\n"
            "Example: `/extract https://example\\.com/video\\.mkv`\n\n"
            "2\\. *Upload \\+ Caption*\n"
            "Upload video and add `/extract` as caption\n\n"
            "3\\. *Reply to Video*\n"
            "Reply with `/extract` to any video message\n\n"
            "4\\. `/status`\n"
            "Show status of all active tasks\n\n"
            "5\\. `/cancelall`\n"
            "Cancel all active downloads\n\n"
            "*Notes:*\n"
            "• Only MKV format is supported\n"
            "• File size limit depends on Telegram's limits\n"
            "• Use `/cancel\\_<id>` to cancel a download\n\n"
            "*How to Use:*\n"
            "1\\. Send video using any of the above methods\n"
            "2\\. Wait for download and extraction\n"
            "3\\. Receive extracted subtitle files\n"
            "4\\. Each subtitle includes language code"
        )
        await update.message.reply_text(
            help_text,
            parse_mode='MarkdownV2',
            reply_to_message_id=update.message.message_id
        )
        
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show status of all active tasks when /status command is issued."""
        try:
            # Delete any existing status messages first
            if self.status_message_id and self.status_chat_id:
                try:
                    await context.bot.delete_message(
                        chat_id=self.status_chat_id,
                        message_id=self.status_message_id
                    )
                except Exception as e:
                    logger.warning(f"Failed to delete old status message: {e}")
            
            # Reset status message tracking
            self.status_message_id = None
            self.status_chat_id = None
            
            # Check for active downloads after cleanup
            if not self.active_downloads:
                await update.message.reply_text(
                    'No active tasks.',
                    reply_to_message_id=update.message.message_id
                )
                return
            
            # Store the command message to reply to it
            self.latest_command_message = update.message
            
            # Create a new status message as reply to the command
            status_message = await update.message.reply_text(
                'Fetching status...',
                reply_to_message_id=update.message.message_id
            )
            
            # Update status message info
            self.status_message_id = status_message.message_id
            self.status_chat_id = update.effective_chat.id
            self.current_page = 0  # Reset to first page
            
            # Also update the status message ID in all active downloads
            for gid in self.active_downloads:
                self.active_downloads[gid].update({
                    "status_message_id": status_message.message_id,
                    "status_chat_id": update.effective_chat.id
                })
            
            # Update status immediately
            await self.update_merged_status(context)
        except Exception as e:
            logger.error(f"Error in status command: {e}")
            await update.message.reply_text(
                'Error fetching status. Please try again.',
                reply_to_message_id=update.message.message_id
            )
        
    async def cancelall_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancel all active downloads when /cancelall command is issued."""
        if not self.active_downloads:
            await update.message.reply_text(
                'No active tasks to cancel.',
                reply_to_message_id=update.message.message_id
            )
            return
            
        # Set canceling flag to prevent status updates
        self.is_canceling = True
        total_tasks = len(self.active_downloads)
        
        try:
            # First delete any existing status message
            if self.status_message_id and self.status_chat_id:
                try:
                    await context.bot.delete_message(
                        chat_id=self.status_chat_id,
                        message_id=self.status_message_id
                    )
                except Exception as e:
                    logger.warning(f"Failed to delete status message: {e}")
            
            # Reset status message tracking
            self.status_message_id = None
            self.status_chat_id = None
            
            # Cancel all downloads using video_downloader cleanup
            self.video_downloader.cleanup()
            
            # Clean up all tracking data
            for gid in list(self.active_downloads.keys()):
                try:
                    if gid in self.active_downloads:
                        self.active_downloads.pop(gid)
                    if gid in self.extraction_progress:
                        self.extraction_progress.pop(gid)
                    if gid in self.upload_progress:
                        self.upload_progress.pop(gid)
                    if gid in self.extraction_speed:
                        self.extraction_speed.pop(gid)
                    if gid in self.upload_speed:
                        self.upload_speed.pop(gid)
                    if gid in self.start_times:
                        self.start_times.pop(gid)
                except Exception as e:
                    logger.error(f"Error cleaning up task data for {gid}: {e}")
            
            # Reply with the number of tasks canceled
            await update.message.reply_text(
                f'Canceled {total_tasks} active tasks.',
                reply_to_message_id=update.message.message_id
            )
        finally:
            # Reset canceling flag
            self.is_canceling = False
    
    async def download_video(self, file_id: str, context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
        """Download video file from Telegram"""
        try:
            file = await context.bot.get_file(file_id)
            download_path = os.path.join(self.download_dir, f"{file_id}.mkv")
            await file.download_to_drive(download_path)
            return download_path
        except Exception as e:
            logger.error(f"Error downloading video: {e}")
            return None
    
    async def handle_video(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle video messages."""
        try:
            if not update.message.document or not update.message.document.file_name.lower().endswith('.mkv'):
                await update.message.reply_text(
                    'Please send an MKV video file.'
                )
                return
            
            # Store the latest command message
            self.latest_command_message = update.message

            # Delete previous status message if it exists
            if self.status_message_id and self.status_chat_id:
                try:
                    await context.bot.delete_message(
                        chat_id=self.status_chat_id,
                        message_id=self.status_message_id
                    )
                except Exception as e:
                    logger.warning(f"Failed to delete old status message: {e}")

            # Create new status message as reply to latest command
            status_message = await update.message.reply_text(
                'Processing your video file...',
                reply_to_message_id=update.message.message_id
            )
            
            # Update status message info
            self.status_message_id = status_message.message_id
            self.status_chat_id = update.effective_chat.id
            
            video_path = await self.download_video(
                update.message.document.file_id,
                context
            )
            
            if not video_path:
                await update.message.reply_text(
                    'Error: Could not download the video file.',
                    reply_to_message_id=update.message.message_id
                )
                # Delete the status message since we're not proceeding
                if self.status_message_id and self.status_chat_id:
                    try:
                        await context.bot.delete_message(
                            chat_id=self.status_chat_id,
                            message_id=self.status_message_id
                        )
                        self.status_message_id = None
                        self.status_chat_id = None
                    except Exception as e:
                        logger.warning(f"Failed to delete status message: {e}")
                return
            
            extractor = SubtitleExtractor(video_path)
            subtitles = extractor.extract_subtitles()
            
            if not subtitles:
                await status_message.edit_text(
                    'No subtitles found in the video file.'
                )
                return
            
            await status_message.edit_text(
                f'Found {len(subtitles)} subtitle tracks. Uploading...'
            )
            
            for sub in subtitles:
                with open(sub['path'], 'rb') as f:
                    filename = f"{sub['language']}.{sub['format']}"
                    # Get the command message ID
                    command_message_id = None
                    try:
                        if isinstance(update, Update) and update.message:
                            command_message_id = update.message.message_id
                        elif hasattr(update, 'message_id'):
                            command_message_id = update.message_id
                    except Exception as e:
                        logger.warning(f"Error getting message ID from update: {e}")

                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=f,
                        filename=filename,
                        caption=f"Language: {sub['language'].upper()}, Format: {sub['format'].upper()}",
                        reply_to_message_id=command_message_id
                    )

            await status_message.edit_text('All subtitles have been extracted and uploaded!')
            extractor.cleanup()
            os.remove(video_path)
            
        except Exception as e:
            logger.error(f"Error processing video: {e}")
            await update.message.reply_text(
                'Sorry, an error occurred while processing your video.',
                reply_to_message_id=update.message.message_id
            )
    
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

    def format_time(self, time_value) -> str:
        """Format time value to MM:SS or HH:MM:SS.
        
        Args:
            time_value: Can be seconds (float/int) or timedelta object
        """
        try:
            if time_value is None:
                return "∞"
                
            # Convert timedelta to seconds if needed
            if isinstance(time_value, datetime.timedelta):
                seconds = int(time_value.total_seconds())
            else:
                try:
                    seconds = int(float(time_value))
                except (ValueError, TypeError):
                    return "∞"
            
            if seconds < 0 or seconds == float('inf'):
                return "∞"
                
            h = seconds // 3600
            m = (seconds % 3600) // 60
            s = seconds % 60
            
            if h > 0:
                return f"{h:d}h{m:02d}m{s:02d}s"
            elif m > 0:
                return f"{m:d}m{s:02d}s"
            else:
                return f"{s:d}s"
                
        except Exception:
            return "∞"  # Escape the infinity symbol for MarkdownV2
            
    def escape_markdownv2(self, text: str) -> str:
        """Escape special characters for Telegram MarkdownV2 format."""
        SPECIAL_CHARACTERS = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', 
                            '-', '=', '|', '{', '}', '.', '!']
        text = str(text)
        
        for char in SPECIAL_CHARACTERS:
            text = text.replace(char, f'\\{char}')
            
        return text
            
    def create_pagination_keyboard(self, total_downloads: int) -> Optional[InlineKeyboardMarkup]:
        """Create pagination keyboard if needed"""
        total_pages = (total_downloads - 1) // self.STATUS_PAGE_SIZE + 1
        if total_pages <= 1:
            return None
            
        buttons = []
        if self.current_page > 0:
            buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"page_{self.current_page - 1}"))
        if self.current_page < total_pages - 1:
            buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"page_{self.current_page + 1}"))
            
        return InlineKeyboardMarkup([buttons]) if buttons else None
            
    async def handle_pagination(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle pagination button callbacks"""
        query = update.callback_query
        data = query.data
        
        if data.startswith('page_'):
            page = int(data.split('_')[1])
            self.current_page = page
            await self.update_merged_status(context)
        elif data == 'close_log':
            await query.message.delete()
            
        await query.answer()

    async def log_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show the last LOG_LINES lines of the log"""
        try:
            log_content = log_buffer.getvalue()
            if not log_content:
                await update.message.reply_text(
                    'Log is empty.',
                    reply_to_message_id=update.message.message_id
                )
                return
            
            log_lines = log_content.splitlines()[-self.LOG_LINES:]
            log_text = '```\n' + '\n'.join(log_lines) + '\n```'

            keyboard = [[InlineKeyboardButton("Close", callback_data="close_log")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                log_text,
                reply_to_message_id=update.message.message_id,
                parse_mode='MarkdownV2',
                reply_markup=reply_markup
            )
        except Exception as e:
            await update.message.reply_text(
                f'Error reading log file: {str(e)}',
                reply_to_message_id=update.message.message_id
            )

    async def get_download_status(self, gid: str) -> str:
        """Generate status text for a single download"""
        download = self.video_downloader.get_download(gid)
        if not download:
            return None

        # Get basic download info
        downloaded = download.completed_length
        total = download.total_length
        speed = download.download_speed
        elapsed = time.time() - self.start_times.get(gid, time.time())

        # Calculate progress and ETA
        progress = (downloaded / total * 100) if total > 0 else 0
        if speed > 0 and total > downloaded:
            eta = (total - downloaded) / speed
        else:
            eta = None

        # Determine status and engine
        if download.is_complete:
            extract_progress = self.extraction_progress.get(gid, 0)
            upload_progress = self.upload_progress.get(gid, 0)
            extract_speed = self.extraction_speed.get(gid, 0)
            upload_speed = self.upload_speed.get(gid, 0)
            
            if extract_progress < 100:
                status = DownloadStatus.EXTRACTING
                engine = "Mkvtoolnix"
                progress = extract_progress
                processed_msg = f"Progress: {self.format_progress_bar(extract_progress)} {extract_progress:.1f}%"
                speed_msg = f"Speed: {self.format_size(extract_speed)}/s"
                if extract_speed > 0:
                    eta = ((100 - extract_progress) / 100 * total) / extract_speed
                else:
                    eta = None
            else:
                status = DownloadStatus.UPLOADING
                engine = "Telegram"
                progress = upload_progress
                processed_msg = f"Progress: {self.format_progress_bar(upload_progress)} {upload_progress:.1f}%"
                speed_msg = f"Speed: {self.format_size(upload_speed)}/s"
                if upload_speed > 0:
                    eta = ((100 - upload_progress) / 100 * total) / upload_speed
                else:
                    eta = None
        else:
            status = DownloadStatus.DOWNLOADING
            engine = "Aria2c"
            try:
                processed_msg = f"Downloaded: {self.format_size(downloaded)} of {self.format_size(total)}"
                speed_msg = f"Speed: {self.format_size(speed)}/s"
            except Exception as e:
                logger.error(f"Error formatting status message: {e}")
                processed_msg = "Download in progress..."
                speed_msg = "Speed: Calculating..."

        # Format the status message
        eta_text = self.format_time(eta) if eta else "∞"

        return (
            f"*{self.escape_markdownv2(download.name)}*\n"
            f"{self.format_progress_bar(progress)} {self.escape_markdownv2(f'{progress:.2f}')}%\n"
            f"Status: {self.escape_markdownv2(status.title())}\n"
            f"{self.escape_markdownv2(processed_msg)}\n"
            f"{self.escape_markdownv2(speed_msg)} \\| ETA: {self.escape_markdownv2(eta_text)}\n"
            f"Running on: {self.escape_markdownv2(engine)} \\| Time: {self.escape_markdownv2(self.format_time(elapsed))}\n"
            f"/cancel\\_{gid}\n"
        )

    async def update_merged_status(self, context: ContextTypes.DEFAULT_TYPE):
        """Update the merged status message showing all active downloads"""
        if not self.status_chat_id or not self.status_message_id or self.is_canceling:
            return

        # Clean up completed or failed downloads
        downloads_to_remove = []
        for gid in list(self.active_downloads.keys()):
            try:
                download = self.video_downloader.get_download(gid)
                # Remove if download is not found, complete, or in final stages
                if (not download or download.is_complete or 
                    gid in self.extraction_progress and self.extraction_progress[gid] >= 100):
                    downloads_to_remove.append(gid)
            except Exception as e:
                logger.warning(f"Error checking download status for {gid}: {e}")
                downloads_to_remove.append(gid)

        # Remove completed downloads and clean up their tracking data
        for gid in downloads_to_remove:
            try:
                if gid in self.active_downloads:
                    del self.active_downloads[gid]
                if gid in self.extraction_progress:
                    del self.extraction_progress[gid]
                if gid in self.upload_progress:
                    del self.upload_progress[gid]
                if gid in self.extraction_speed:
                    del self.extraction_speed[gid]
                if gid in self.upload_speed:
                    del self.upload_speed[gid]
                if gid in self.start_times:
                    del self.start_times[gid]
            except Exception as e:
                logger.warning(f"Error cleaning up task {gid}: {e}")

        # Get remaining active downloads
        downloads = list(self.active_downloads.keys())
        if not downloads:
            # If no active downloads, delete the status message
            try:
                await context.bot.delete_message(
                    chat_id=self.status_chat_id,
                    message_id=self.status_message_id
                )
            except Exception as e:
                logger.warning(f"Failed to delete status message: {e}")
            self.status_message_id = None
            self.status_chat_id = None
            return

        # Calculate pagination
        total_downloads = len(downloads)
        total_pages = (total_downloads - 1) // self.STATUS_PAGE_SIZE + 1
        
        # Ensure current_page is valid
        self.current_page = min(max(0, self.current_page), total_pages - 1)
        
        # Get downloads for current page
        start_idx = self.current_page * self.STATUS_PAGE_SIZE
        end_idx = start_idx + self.STATUS_PAGE_SIZE
        current_downloads = downloads[start_idx:end_idx]
        
        # Build status messages
        status_texts = []
        for gid in current_downloads:
            status = await self.get_download_status(gid)
            if status:
                status_texts.append(status)

        if not status_texts:
            return

        # Add page indicator if multiple pages
        # Add page indicator if more than one page exists
        if total_pages > 1:
            page_text = f"Page {self.current_page + 1}/{total_pages}"
            status_texts.append(self.escape_markdownv2(page_text))

        # Add Bot Stats at the end
        sys_stats = self.system_stats.get_stats()
        dl_speed = ul_speed = 0
        for gid in self.active_downloads:
            download = self.video_downloader.get_download(gid)
            if download:
                dl_speed += download.download_speed if hasattr(download, 'download_speed') else 0
                ul_speed += download.upload_speed if hasattr(download, 'upload_speed') else 0
        
        bot_stats = (
            f"\n\nBot Stats\n"
            f"CPU: {self.escape_markdownv2(sys_stats['cpu'])} \\| F: {self.escape_markdownv2(sys_stats['disk'])}\n"
            f"RAM: {self.escape_markdownv2(sys_stats['ram'])} \\| UPTIME: {self.escape_markdownv2(sys_stats['uptime'])}\n"
            f"DL: {self.escape_markdownv2(self.format_size(dl_speed))}/s \\| UL: {self.escape_markdownv2(self.format_size(ul_speed))}/s"
        )

        # Combine all status texts
        full_status = "\n\n".join(status_texts) + bot_stats

        try:
            await context.bot.edit_message_text(
                chat_id=self.status_chat_id,
                message_id=self.status_message_id,
                text=full_status,
                parse_mode='MarkdownV2',
                reply_markup=self.create_pagination_keyboard(len(downloads))
            )
        except tg_error.BadRequest as e:
            if "message is not modified" not in str(e).lower():
                logger.error(f"Error updating status message: {e}")
        except Exception as e:
            logger.error(f"Error updating status message: {e}")

    async def update_progress_status(self, chat_id: int, message_id: int, gid: str, context: ContextTypes.DEFAULT_TYPE):
        """Update download status message every 10 seconds."""
        last_update = 0
        start_time = time.time()
        
        try:
            while True:
                current_time = time.time()
                download = self.video_downloader.get_download(gid)
                
                if not download:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text='Error: Download not found or cancelled.'
                    )
                    return
                    
                if download.has_failed:
                    error_msg = download.error_message if hasattr(download, 'error_message') else 'Unknown error'
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=f'Error: Download failed.\nReason: {error_msg}'
                    )
                    return
                    
                if download.is_complete:
                    return

                # Only update if enough time has passed
                if current_time - last_update < self.UPDATE_INTERVAL:
                    await asyncio.sleep(0.5)
                    continue

                # Handle potential errors when accessing download properties
                try:
                    progress = download.progress
                    downloaded = download.completed_length
                    total = download.total_length
                    speed = download.download_speed
                    current_time = time.time()
                    elapsed = current_time - start_time

                    # Calculate ETA based on current phase
                    if total > 0 and speed > 0:
                        remaining_bytes = total - downloaded
                        eta = remaining_bytes / speed
                    else:
                        eta = -1
                except Exception as e:
                    logger.error(f"Error getting download properties: {e}")
                    # Set default values if we can't get the actual values
                    progress = 0
                    downloaded = 0
                    total = 0
                    speed = 0
                    eta = -1
                    current_time = time.time()
                    elapsed = current_time - start_time

                # Determine current phase and engine
                progress_status = "Downloading"
                engine = "Aria2c"
                
                try:
                    processed_msg = f"Downloaded: {self.format_size(downloaded)} of {self.format_size(total)}"
                    speed_msg = f"Speed: {self.format_size(speed)}/s"
                except Exception as e:
                    logger.error(f"Error formatting status message: {e}")
                    processed_msg = "Download in progress..."
                    speed_msg = "Speed: Calculating..."
                
                if download.is_complete:
                    # Get actual progress values
                    extract_progress = self.extraction_progress.get(gid, 0)
                    upload_progress = self.upload_progress.get(gid, 0)
                    extract_speed = self.extraction_speed.get(gid, 0)
                    upload_speed = self.upload_speed.get(gid, 0)
                    
                    if extract_progress < 100:  # Extraction phase
                        progress_status = "Extracting Subtitles"
                        engine = "Mkvtoolnix"
                        processed_msg = f"Progress: {self.format_progress_bar(extract_progress)} {extract_progress:.1f}%"
                        speed_msg = f"Speed: {self.format_size(extract_speed)}/s"
                    else:  # Upload phase
                        progress_status = "Uploading"
                        engine = "Telegram"
                        processed_msg = f"Progress: {self.format_progress_bar(upload_progress)} {upload_progress:.1f}%"
                        speed_msg = f"Speed: {self.format_size(upload_speed)}/s"

                # Calculate cumulative speeds
                sys_stats = self.system_stats.get_stats()
                dl_speed = 0
                ul_speed = 0
                for active_gid in self.active_downloads.keys():
                    active_dl = self.video_downloader.get_download(active_gid)
                    if active_dl:
                        dl_speed += active_dl.download_speed if hasattr(active_dl, 'download_speed') else 0
                        ul_speed += active_dl.upload_speed if hasattr(active_dl, 'upload_speed') else 0
                
                status = (
                    f"*{self.escape_markdownv2(download.name)}*\n"
                    f"{self.format_progress_bar(progress)} {self.escape_markdownv2(f'{progress:.2f}')}%\n"
                    f"Status: {self.escape_markdownv2(progress_status)}\n"
                    f"{self.escape_markdownv2(processed_msg)}\n"
                    f"{self.escape_markdownv2(speed_msg)} \\| ETA: {self.escape_markdownv2(self.format_time(eta))}\n"
                    f"Running on: {self.escape_markdownv2(engine)} \\| Time: {self.escape_markdownv2(self.format_time(elapsed))}\n"
                    f"/cancel\\_{gid}\n\n"
                    f"Bot Stats\n"
                    f"CPU: {self.escape_markdownv2(sys_stats['cpu'])} \\| F: {self.escape_markdownv2(sys_stats['disk'])}\n"
                    f"RAM: {self.escape_markdownv2(sys_stats['ram'])} \\| UPTIME: {self.escape_markdownv2(sys_stats['uptime'])}\n"
                    f"DL: {self.escape_markdownv2(self.format_size(dl_speed))}/s \\| UL: {self.escape_markdownv2(self.format_size(ul_speed))}/s"
                )

                # Update the merged status message if enough time has passed
                if current_time - last_update >= self.UPDATE_INTERVAL:
                    await self.update_merged_status(context)
                    last_update = current_time

        except Exception as e:
            logger.error(f"Error updating status: {e}")
        finally:
            if gid in self.active_downloads:
                del self.active_downloads[gid]
                self.start_times.pop(gid, None)  # Remove start time

    async def url_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle URL input"""
        try:
            message = context.user_data.get('url_message', update.message)
            url = message.text.strip()
            
            if not url.lower().endswith('.mkv'):
                await message.reply_text(
                    'Error: Only MKV files are supported. Please provide a direct link to an MKV file.',
                    reply_to_message_id=message.message_id
                )
                return
                
            # Store the latest command message
            self.latest_command_message = update.message

            # Delete previous status message if it exists
            if self.status_message_id and self.status_chat_id:
                try:
                    await context.bot.delete_message(
                        chat_id=self.status_chat_id,
                        message_id=self.status_message_id
                    )
                except Exception as e:
                    logger.warning(f"Failed to delete old status message: {e}")

            # Create new status message as reply to latest command
            status_message = await update.message.reply_text(
                'Starting download...',
                reply_to_message_id=update.message.message_id
            )
            
            # Update status message info
            self.status_message_id = status_message.message_id
            self.status_chat_id = update.effective_chat.id
            
            gid = self.video_downloader.start_download(url)
            if not gid:
                await status_message.edit_text(
                    'Error: Could not start download. Please check if the URL is valid and accessible.'
                )
                return
                
            # Record start time for the download
            self.start_times[gid] = time.time()
            
            logger.info(f"Started download with GID: {gid} for URL: {url}")
            
            # Update or create central status message
            if not self.status_message_id:
                self.status_message_id = status_message.message_id
                self.status_chat_id = update.effective_chat.id
            else:
                try:
                    # Try to update existing status message
                    status_message = await context.bot.edit_message_text(
                        text='Processing tasks...',
                        chat_id=self.status_chat_id,
                        message_id=self.status_message_id
                    )
                except Exception as e:
                    logger.warning(f"Could not update existing status message: {e}")
                    # Create new status message
                    status_message = await update.message.reply_text(
                        'Processing tasks...',
                        reply_to_message_id=update.message.message_id
                    )
                    self.status_message_id = status_message.message_id
                    self.status_chat_id = update.effective_chat.id
            
            # Store download info
            self.active_downloads[gid] = {
                "message_id": status_message.message_id,
                "chat_id": update.effective_chat.id,
                "command_message_id": self.latest_command_message.message_id
            }
            download_complete = asyncio.Event()
            
            async def update_status_wrapper():
                try:
                    await self.update_progress_status(
                        update.effective_chat.id,
                        status_message.message_id,
                        gid,
                        context
                    )
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"Status update task error: {e}", exc_info=True)
                finally:
                    download_complete.set()
            
            status_task = asyncio.create_task(update_status_wrapper())
            
            try:
                await download_complete.wait()
                
                download = self.video_downloader.get_download(gid)
                if not download or self.is_canceling:
                    # Don't try to edit message if canceling
                    return
                    
                if download.is_complete:
                    video_path = os.path.join(self.download_dir, download.name)
                    if os.path.exists(video_path):
                        try:
                            await self.process_video_file(video_path, update, context, status_message, gid)
                        finally:
                            # Always try to remove the download from aria2 if not canceling
                            if not self.is_canceling:
                                try:
                                    self.video_downloader.aria2.remove([download], force=True)
                                except Exception as e:
                                    logger.warning(f"Failed to remove download from aria2: {e}")
                                # Clean up progress tracking
                                self.extraction_progress.pop(gid, None)
                                self.upload_progress.pop(gid, None)
                                self.extraction_speed.pop(gid, None)
                                self.upload_speed.pop(gid, None)
                        return
                    else:
                        if not self.is_canceling:
                            await status_message.edit_text('Error: Downloaded file not found.')
                        return
                
                if download.has_failed and not self.is_canceling:
                    error_msg = download.error_message if hasattr(download, 'error_message') else 'Unknown error'
                    await status_message.edit_text(f'Download failed.\nReason: {error_msg}')
                    # Clean up failed download from aria2
                    try:
                        self.video_downloader.aria2.remove([download], force=True)
                    except Exception as e:
                        logger.warning(f"Failed to remove failed download from aria2: {e}")
                    return
                
                if hasattr(download, 'progress') and download.progress is not None and not self.is_canceling:
                    await download_complete.wait()
                else:
                    if not self.is_canceling:
                        await status_message.edit_text('Error: Could not determine download status.')
                    
            except Exception as e:
                logger.error(f"Error in download handler: {str(e)}", exc_info=True)
                if not self.is_canceling:
                    await status_message.edit_text('Error: An unexpected error occurred during the download.')
            finally:
                status_task.cancel()
                try:
                    await status_task
                except asyncio.CancelledError:
                    pass
                
                if gid in self.active_downloads:
                    del self.active_downloads[gid]
                    self.start_times.pop(gid, None)  # Remove start time
                    
        except Exception as e:
            logger.error(f"Error in URL handler: {str(e)}", exc_info=e)
            await update.message.reply_text(
                'Sorry, an error occurred while processing your request. Please try again later.'
            )
    
    async def process_video_file(self, video_path: str, update: Update, context: ContextTypes.DEFAULT_TYPE, status_message, gid: str = None):
        """Process the video file and extract subtitles.
        
        Args:
            video_path (str): Path to the video file
            update (Update): The update object
            context (ContextTypes.DEFAULT_TYPE): The context object
            status_message: The status message object
            gid (str, optional): The download task ID. Defaults to None.
        """
        extractor = None
        try:
            extractor = SubtitleExtractor(video_path)
            subtitles = extractor.extract_subtitles()
            
            if not subtitles:
                await status_message.edit_text(
                    'No subtitles found in the video file.'
                )
                return False
            
            total_subs = len(subtitles)
            current_sub = 0
            total_size = 0
            
            # Calculate total size of all subtitle files
            for sub in subtitles:
                total_size += os.path.getsize(sub['path'])
            
            start_time = time.time()
            processed_size = 0
            
            for sub in subtitles:
                current_sub += 1
                sub_size = os.path.getsize(sub['path'])
                
                # Update extraction progress only if we have a task ID
                task_id = str(gid) if gid is not None else None
                if task_id:
                    self.extraction_progress[task_id] = (current_sub / total_subs) * 100
                    elapsed = time.time() - start_time
                    if elapsed > 0:
                        self.extraction_speed[task_id] = int(processed_size / elapsed)
                
                try:
                    with open(sub['path'], 'rb') as f:
                        start_upload = time.time()
                        
                        # Get the message to reply to
                        reply_to_id = None
                        try:
                            task_id = str(gid) if gid is not None else None
                            # First try to get command message ID from task info if we have a valid task ID
                            if task_id and task_id in self.active_downloads:
                                reply_to_id = self.active_downloads[task_id].get('command_message_id')
                            
                            # Fallback to update message ID if needed
                            if not reply_to_id:
                                if isinstance(update, Update) and update.message:
                                    reply_to_id = update.message.message_id
                                elif hasattr(update, 'message_id'):
                                    reply_to_id = update.message_id
                        except Exception as e:
                            logger.warning(f"Error getting message ID for reply: {e}")
                            reply_to_id = None
                            
                        await context.bot.send_document(
                            chat_id=update.effective_chat.id,
                            document=f,
                            filename=f"{sub['language']}.{sub['format']}",
                            caption=f"Language: {sub['language'].upper()}, Format: {sub['format'].upper()}",
                            reply_to_message_id=reply_to_id
                        )
                        upload_time = time.time() - start_upload
                        # Update upload progress only if we have a valid task ID
                        task_id = str(gid) if gid is not None else None
                        if task_id and upload_time > 0:
                            self.upload_speed[task_id] = int(sub_size / upload_time)
                            processed_size += sub_size
                            self.upload_progress[task_id] = (processed_size / total_size) * 100
                except Exception as e:
                    logger.error(f"Error uploading subtitle: {e}")
                    raise
            
            # Clean up task tracking after successful processing
            if gid:
                try:
                    # Remove from active_downloads to stop status updates
                    if gid in self.active_downloads:
                        del self.active_downloads[gid]
                        
                    # Remove all tracking data
                    for tracker in [self.extraction_progress, self.upload_progress,
                                  self.extraction_speed, self.upload_speed, 
                                  self.start_times]:
                        if gid in tracker:
                            del tracker[gid]
                            
                    # Update status message to reflect changes
                    await self.update_merged_status(context)
                except Exception as e:
                    logger.warning(f"Error cleaning up task {gid}: {e}")
            
            return True  # Indicate successful processing
            
        except asyncio.TimeoutError:
            # If we have a timeout but files were uploaded, consider it successful
            return True
        except Exception as e:
            logger.error(f"Error processing video: {e}")
            # Only show error if we really failed to process
            if not (isinstance(e, asyncio.TimeoutError) and extractor):
                await status_message.edit_text(
                    'Sorry, an error occurred while processing your video.'
                )
            
            # Clean up task on error too
            if gid:
                try:
                    if gid in self.active_downloads:
                        del self.active_downloads[gid]
                    await self.update_merged_status(context)
                except Exception as cleanup_error:
                    logger.warning(f"Error cleaning up failed task {gid}: {cleanup_error}")
                    
            return False
            
        finally:
            # Always clean up in all cases
            try:
                if extractor:
                    extractor.cleanup()
            except Exception as cleanup_error:
                logger.warning(f"Error cleaning up extractor: {cleanup_error}")
                
            try:
                if os.path.exists(video_path):
                    os.remove(video_path)
            except Exception as cleanup_error:
                logger.warning(f"Error removing video file: {cleanup_error}")
    
    async def handle_video(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle video file uploads"""
        try:
            message = update if isinstance(update, Message) else update.message
            document = message.document
            
            if not document and message.video:
                document = message.video
            
            if not document or (
                not document.file_name.lower().endswith('.mkv') and
                not document.mime_type == 'video/x-matroska'
            ):
                await message.reply_text(
                    'Please send an MKV video file or use /help to see supported formats.'
                )
                return
            
            # Store the latest command message
            self.latest_command_message = message

            # Delete previous status message if it exists
            if self.status_message_id and self.status_chat_id:
                try:
                    await context.bot.delete_message(
                        chat_id=self.status_chat_id,
                        message_id=self.status_message_id
                    )
                except Exception as e:
                    logger.warning(f"Failed to delete old status message: {e}")

            # Create new status message as reply to latest command
            status_message = await message.reply_text(
                'Processing your video file...',
                reply_to_message_id=message.message_id
            )
            
            # Update status message info
            self.status_message_id = status_message.message_id
            self.status_chat_id = message.chat.id
            
            video_path = await self.download_video(
                document.file_id,
                context
            )
            
            if not video_path:
                await status_message.edit_text(
                    'Error: Could not download the video file.'
                )
                return
            
            # Generate a task ID for tracking
            task_id = f"upload_{str(int(time.time()))}"
            
            # Store task info
            self.active_downloads[task_id] = {
                "message_id": status_message.message_id,
                "chat_id": message.chat.id,
                "command_message_id": message.message_id
            }
            
            try:
                await self.process_video_file(video_path, message, context, status_message, task_id)
            finally:
                # Clean up task tracking
                if task_id in self.active_downloads:
                    del self.active_downloads[task_id]
            
        except Exception as e:
            logger.error(f"Error processing video: {e}")
            if isinstance(update, Message):
                await update.reply_text(
                    'Sorry, an error occurred while processing your video.'
                )
            else:
                await update.message.reply_text(
                    'Sorry, an error occurred while processing your video.'
                )
    
    async def handle_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /cancel_<gid> commands"""
        try:
            command = update.message.text.strip()
            
            if command.startswith('/cancel_'):
                gid = command[8:].lstrip('_')
            else:
                gid = None
            
            if not gid:
                await update.message.reply_text(
                'Invalid cancel command. Use /cancel_<id> to cancel a download.',
                reply_to_message_id=update.message.message_id
            )
                return
            
            logger.info(f"Attempting to cancel download with GID: {gid}")
            
            if self.video_downloader.cancel_download(gid):
                await update.message.reply_text(
                    f'GID {gid} cancelled.',
                    reply_to_message_id=update.message.message_id
                )
                
                if gid in self.active_downloads:
                    msg_id = self.active_downloads[gid]
                    try:
                        await context.bot.edit_message_text(
                            chat_id=update.effective_chat.id,
                            message_id=msg_id,
                            text=f'GID {gid} cancelled.'
                        )
                    except Exception as e:
                        logger.error(f"Failed to update status message: {e}")
                        pass
                    
                    del self.active_downloads[gid]
                    self.start_times.pop(gid, None)  # Remove start time
            else:
                await update.message.reply_text(
                    f'Could not cancel download {gid}. It may have already completed or failed.'
                )
                
        except Exception as e:
            logger.error(f"Error in cancel handler: {e}", exc_info=True)
            await update.message.reply_text(
                'Error processing cancel command. Please check the ID and try again.'
            )

    async def extract_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /extract command in various contexts"""
        try:
            if update.message.reply_to_message and (
                update.message.reply_to_message.document or 
                update.message.reply_to_message.video
            ):
                msg = update.message.reply_to_message
                if msg.document:
                    await self.handle_video(msg, context)
                elif msg.video:
                    msg.document = msg.video
                    await self.handle_video(msg, context)
                return

            if context.args:
                try:
                    url = " ".join(context.args).strip()
                    if not url.startswith(('http://', 'https://')):
                        url = 'https://' + url
                    
                    class UrlMessage:
                        def __init__(self, original_message, url):
                            self.text = url
                            self.message_id = original_message.message_id
                            self.chat = original_message.chat
                            self.reply_text = original_message.reply_text
                            
                    url = url.replace(' ', '%20')
                    context.user_data['url_message'] = UrlMessage(update.message, url)
                    await self.url_handler(update, context)
                    return
                except Exception as e:
                    logger.error(f"Error processing URL: {str(e)}", exc_info=e)
                    await update.message.reply_text(
                        'Error: Invalid URL format. Please provide a direct link to an MKV file.'
                    )
                    return

            await update.message.reply_text(
                "Please use /extract in one of these ways:\n\n"
                "1. `/extract` URL - Extract from URL\n"
                "Example: `/extract https://example.com/video.mkv`\n\n"
                "2. Upload video with `/extract` as caption\n"
                "3. Reply `/extract` to a video message\n\n"
                "*Note:* Only MKV format is supported",
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"Error in extract command: {str(e)}", exc_info=e)
            await update.message.reply_text(
                'Sorry, an error occurred while processing your command. Please try again.'
            )

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle errors in the telegram bot."""
        try:
            error_msg = f"Error: {str(context.error)}"
            logger.error(error_msg, exc_info=context.error)
            if update and isinstance(update, Update) and update.effective_message:
                try:
                    await update.effective_message.reply_text(
                        "Sorry, something went wrong. Please try again later.",
                        reply_to_message_id=update.effective_message.message_id
                    )
                except Exception as e:
                    logger.error(f"Failed to send error message to user: {e}", exc_info=e)
        except Exception as e:
            logger.error(f"Error in error handler: {e}", exc_info=e)


    async def init_bot(self):
        """Initialize the bot and set up handlers."""
        application = (
            Application.builder()
            .token(self.token)
            .concurrent_updates(True)
            .build()
        )
        
        await application.initialize()
        await application.bot.initialize()
        
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("help", self.help_command))
        # Add main commands and shortcuts
        application.add_handler(CommandHandler(["extract", "e"], self.extract_command))
        application.add_handler(CommandHandler("status", self.status_command))
        application.add_handler(CommandHandler("cancelall", self.cancelall_command))
        application.add_handler(CommandHandler("log", self.log_command))
        
        application.add_handler(MessageHandler(
            filters.Regex(r'^/cancel_[a-zA-Z0-9]+$'),
            self.handle_cancel
        ))
        
        application.add_handler(MessageHandler(
            (filters.Document.VIDEO | filters.Document.ALL) & filters.Caption('^/extract') & ~filters.COMMAND,
            self.handle_video,
            block=False
        ))
        
        application.add_handler(MessageHandler(
            (filters.Document.VIDEO | filters.Document.ALL) & ~filters.COMMAND,
            self.handle_video,
            block=False
        ))
        
        # Add pagination callback handler
        application.add_handler(CallbackQueryHandler(self.handle_pagination, pattern="^page_"))
        
        application.add_error_handler(self.error_handler)
        
        return application

    def run(self):
        """Start the bot."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            application = loop.run_until_complete(self.init_bot())
            application.run_polling(
                allowed_updates=["message", "callback_query"],
                drop_pending_updates=True
            )
        finally:
            loop.close()