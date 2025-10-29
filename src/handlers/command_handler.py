import os, logging, asyncio, httpx, httpcore
from typing import Optional
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.error import BadRequest, TimedOut, NetworkError, RetryAfter
from uuid import uuid4
from ..models.task import SubtitleTask
from ..models.task_status import TaskStatus
from ..services.task_queue import TaskQueue
from ..services.task_processor import TaskProcessor
from ..services.aria2_service import Aria2Service
from ..handlers.message_handler import MessageHandler
from ..utils.logging_config import configure_logging

logger = logging.getLogger(__name__)

# Retry configuration
MAX_RETRIES = 3
BASE_RETRY_DELAY = 2

class CommandHandler:
    """Handles bot commands and coordinates tasks"""
    
    @staticmethod
    def _generate_task_id() -> str:
        """Generate a short unique alphanumeric task ID with mixed case."""
        # Get the hex representation of UUID
        uid = str(uuid4()).replace('-', '')[:12]  # Using 12 chars for more variety
        # Convert to integer and format as alphanumeric
        num = int(uid, 16)
        # Use digits 0-9, lowercase a-z, and uppercase A-Z (total 62 chars)
        alpha = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
        result = ''
        while num:
            num, rem = divmod(num, 62)
            result = alpha[rem] + result
        # Pad with zeros to ensure consistent length (at least 6 chars)
        return result.zfill(6)

    def __init__(self, task_queue: TaskQueue, aria2_service: Aria2Service):
        """Initialize command handler with a task queue.
        The handler will manage its own instances of other required services."""
        self.task_queue = task_queue
        self.download_dir = os.getenv('DOWNLOAD_DIR', '/tmp/downloads/')
        self.update_interval = float(os.getenv('UPDATE_INTERVAL', '10.0'))
        self.task_processor = TaskProcessor(self.download_dir, aria2_service)
        self.message_handler = MessageHandler()
        self.job_manager = None
        self.log_buffer = configure_logging(os.getenv('LOG_LEVEL', 'INFO'))
        self._task_locks = {}
        
        for status in TaskStatus:
            self.task_queue.add_status_handler(
                status, 
                self.handle_task_status_change
            )
            
    def set_job_manager(self, job_manager):
        """Set the job manager for scheduling updates"""
        self.job_manager = job_manager
            
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command"""
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
        
    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command"""
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
            "6\\. `/log`\n"
            "Show bot operation logs\n\n"
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

    async def _check_duplicate_task(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Check if a task with the same URL or file is already being processed.
        Returns True if duplicate found."""
        if context.args:
            url = " ".join(context.args).strip()
            existing_tasks = [t for t in self.task_queue.get_all_tasks() 
                            if t.url == url and t.status not in 
                            [TaskStatus.COMPLETED, TaskStatus.ERROR, TaskStatus.CANCELED]]
            if existing_tasks:
                asyncio.create_task(context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="This URL is already being processed.",
                    reply_to_message_id=update.effective_message.message_id
                ))
                return True
        elif update.message.reply_to_message and update.message.reply_to_message.document:
            file_id = update.message.reply_to_message.document.file_id
            existing_tasks = [t for t in self.task_queue.get_all_tasks()
                            if t.metadata.get('file_id') == file_id and t.status not in 
                            [TaskStatus.COMPLETED, TaskStatus.ERROR, TaskStatus.CANCELED]]
            if existing_tasks:
                asyncio.create_task(context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="This file is already being processed.",
                    reply_to_message_id=update.effective_message.message_id
                ))
                return True
        return False

    async def handle_extract(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /extract command"""
        try:
            duplicate_check = asyncio.create_task(self._check_duplicate_task(update, context))
            task_creation = asyncio.create_task(self._create_task_from_update(update, context))
            is_duplicate = await duplicate_check
            
            if is_duplicate:
                task_creation.cancel()
                return
                
            task = await task_creation
            if not task:
                return
                
            if update.message.reply_to_message and update.message.reply_to_message.document:
                task.metadata['file_id'] = update.message.reply_to_message.document.file_id
                
            self.task_processor.active_tasks[task.task_id] = {
                "task": task,
                "context": context
            }

            if self.message_handler.status_message_id:
                try:
                    await context.bot.delete_message(
                        chat_id=self.message_handler.status_chat_id,
                        message_id=self.message_handler.status_message_id
                    )
                except Exception as e:
                    logger.warning(f"Failed to delete old status message: {e}")
                finally:
                    self.message_handler.status_message_id = None
                    self.message_handler.status_chat_id = None
            
            self.task_queue.add_task(task)
            
            message = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Processing...",
                reply_to_message_id=update.effective_message.message_id
            )
            self.message_handler.status_message_id = message.message_id
            self.message_handler.status_chat_id = update.effective_chat.id
            self.message_handler.status_message = message
            
            active_tasks = [t for t in self.task_queue.get_all_tasks()
                            if t.status not in [TaskStatus.COMPLETED, TaskStatus.ERROR, TaskStatus.CANCELED]]
            for task in active_tasks:
                if task.task_id not in self.task_processor.active_tasks:
                    self.task_processor.active_tasks[task.task_id] = {"task": task, "context": context}
            
            if self.job_manager:
                await self.job_manager.start_job(
                    'status_update',
                    lambda: self.message_handler.update_status_message(
                        self.task_queue.get_all_tasks(),
                        context
                    ),
                    self.update_interval
                )
                
            await self.message_handler.update_status_message(
                self.task_queue.get_all_tasks(),
                context
            )
            
        except Exception as e:
            logger.error(f"Error handling extract command: {e}")
            await self.message_handler.send_error_message(
                update.effective_chat.id,
                update.effective_message.message_id,
                str(e),
                context
            )
            
    async def handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status command"""
        try:
            tasks = [t for t in self.task_queue.get_all_tasks() 
                    if t.status not in [TaskStatus.COMPLETED, TaskStatus.ERROR, TaskStatus.CANCELED]]
            
            if self.message_handler.status_message_id:
                try:
                    await context.bot.delete_message(
                        chat_id=self.message_handler.status_chat_id,
                        message_id=self.message_handler.status_message_id
                    )
                except Exception as e:
                    logger.warning(f"Failed to delete old status message: {e}")
                finally:
                    self.message_handler.status_message_id = None
                    self.message_handler.status_chat_id = None
            
            if not tasks:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="No active tasks.",
                    reply_to_message_id=update.effective_message.message_id
                )
                return
            
            keyboard = self.message_handler.create_pagination_keyboard(len(tasks))
            
            message = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Processing...",
                reply_to_message_id=update.effective_message.message_id,
                reply_markup=keyboard
            )
            
            self.message_handler.status_message_id = message.message_id
            self.message_handler.status_chat_id = update.effective_chat.id
            self.message_handler.status_message = message
            
            for task in tasks:
                if task.task_id not in self.task_processor.active_tasks:
                    self.task_processor.active_tasks[task.task_id] = {
                        "task": task,
                        "context": context,
                        "message": message
                    }
            
            if self.job_manager:
                await self.job_manager.start_job(
                    'status_update',
                    lambda: self.message_handler.update_status_message(
                        self.task_queue.get_all_tasks(),
                        context
                    ),
                    self.update_interval
                )
            
            await self.message_handler.update_status_message(
                self.task_queue.get_all_tasks(),
                context
            )
            
        except Exception as e:
            logger.error(f"Error handling status command: {e}")
            await self.message_handler.send_error_message(
                update.effective_chat.id,
                update.effective_message.message_id,
                str(e),
                context
            )
            
    async def handle_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /cancel command"""
        try:
            task_id = context.match.group(1)
            if not await self.task_queue.cancel_task(task_id):
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"Task {task_id} not found.",
                    reply_to_message_id=update.effective_message.message_id
                )
                return
                
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"Task {task_id} canceled.",
                reply_to_message_id=update.effective_message.message_id
            )
            
            active_tasks = [t for t in self.task_queue.get_all_tasks() 
                         if t.status not in [TaskStatus.COMPLETED, TaskStatus.ERROR, TaskStatus.CANCELED]]
            
            if not active_tasks and self.message_handler.status_message_id:
                try:
                    if self.message_handler.status_message:
                        try:
                            await self.message_handler.status_message.delete()
                        except BadRequest as e:
                            if "message to delete not found" not in str(e).lower():
                                logger.warning(f"Failed to delete status message: {e}")
                except Exception as e:
                    logger.warning(f"Error during status message cleanup: {e}")
                finally:
                    self.message_handler.status_message = None
                    self.message_handler.status_message_id = None
                    self.message_handler.status_chat_id = None
            else:
                await self.message_handler.update_status_message(
                    self.task_queue.get_all_tasks(),
                    context
                )
                
        except Exception as e:
            logger.error(f"Error handling cancel command: {e}")
            await self.message_handler.send_error_message(
                update.effective_chat.id,
                update.effective_message.message_id,
                str(e),
                context
            )
            
    async def handle_page_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle page navigation callback queries"""
        query = update.callback_query
        try:
            page = int(query.data.split('_')[1])
            self.message_handler.current_page = page
            tasks = [t for t in self.task_queue.get_all_tasks() 
                    if t.status not in [TaskStatus.COMPLETED, TaskStatus.ERROR, TaskStatus.CANCELED]]
            
            status_text = self.message_handler._format_status_message(tasks)
            keyboard = self.message_handler.create_pagination_keyboard(len(tasks))
            
            await query.message.edit_text(
                text=status_text,
                parse_mode='MarkdownV2',
                reply_markup=keyboard
            )
            await query.answer()
        except Exception as e:
            logger.error(f"Error handling page navigation: {e}")
            await query.answer("Error navigating pages", show_alert=True)
            
    async def handle_close_logs(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle close button on log messages"""
        query = update.callback_query
        try:
            await query.message.delete()
            await query.answer()
        except Exception as e:
            logger.error(f"Error handling close logs: {e}")
            await query.answer("Error closing logs", show_alert=True)
            
    async def handle_cancelall(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /cancelall command"""
        try:
            count = await self.task_queue.cancel_all_tasks()
            
            if self.message_handler.status_message:
                try:
                    await self.message_handler.status_message.delete()
                except Exception as e:
                    logger.warning(f"Failed to delete status message: {e}")
                finally:
                    self.message_handler.status_message = None
                    self.message_handler.status_message_id = None
                    self.message_handler.status_chat_id = None
            
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"Canceled {count} active tasks.",
                reply_to_message_id=update.effective_message.message_id
            )
            
        except Exception as e:
            logger.error(f"Error handling cancelall command: {e}")
            await self.message_handler.send_error_message(
                update.effective_chat.id,
                update.effective_message.message_id,
                str(e),
                context
            )
            
    async def handle_task_status_change(self, task: SubtitleTask) -> None:
        """Handle task status changes.
        This is the public interface for handling task status changes used by TaskQueue."""
        
        if task.task_id not in self._task_locks:
            self._task_locks[task.task_id] = asyncio.Lock()
            logger.debug(f"Created new lock for task {task.task_id}")

        try:
            acquired = False
            for _ in range(3):
                if await asyncio.wait_for(self._task_locks[task.task_id].acquire(), timeout=0.1):
                    acquired = True
                    break
                await asyncio.sleep(0.1)
            
            if not acquired:
                logger.debug(f"Task {task.task_id} is being handled by another instance, skipping")
                return
                
            if task.task_id not in self.task_processor.active_tasks:
                logger.debug(f"Task {task.task_id} already handled, skipping")
                self._task_locks[task.task_id].release()
                return

            logger.debug(f"Handling status change for task {task.task_id}: {task.status.name}")
            if task.status == TaskStatus.WAITING:
                context = self.task_processor.active_tasks.get(task.task_id, {}).get('context')
                if not context:
                    logger.error(f"No context found for task {task.task_id}")
                    return
                
                self._task_locks[task.task_id].release()
                await self.task_processor.process_task(task, context)
                
            elif task.status == TaskStatus.COMPLETED:
                logger.info(f"Task {task.task_id} completed successfully")
                context = self.task_processor.active_tasks.get(task.task_id, {}).get('context')
                if not context:
                    logger.warning(f"No context found for completed task {task.task_id}. Maybe already cleaned up.")
                    return
                try:
                    await self._upload_subtitles(task, context)
                    logger.info(f"Successfully uploaded subtitles for task {task.task_id}")
                    self._cleanup_task_files(task)
                    self.task_processor.active_tasks.pop(task.task_id, None)
                except Exception as e:
                    logger.error(f"Failed to upload subtitles: {e}")
                    task.status = TaskStatus.ERROR
                    task.error_message = str(e)
                    raise
                        
            elif task.status == TaskStatus.ERROR and self._get_context():
                try:
                    await self.message_handler.send_error_message(
                        task.chat_id,
                        task.command_message_id,
                        task.error_message or "Unknown error",
                        self._get_context()
                    )
                finally:
                    if task.task_id in self.task_processor.active_tasks:
                        self._cleanup_task_files(task)
                        self.task_processor.active_tasks.pop(task.task_id, None)
                        
            elif task.status == TaskStatus.CANCELED:
                if task.task_id in self.task_processor.active_tasks:
                    self._cleanup_task_files(task)
                    self.task_processor.active_tasks.pop(task.task_id, None)
            
            retries = 0
            while retries < MAX_RETRIES:
                try:
                    await self.message_handler.update_status_message(
                        self.task_queue.get_all_tasks(),
                        self._get_context()
                    )
                    break
                except (TimedOut, NetworkError, httpcore.ReadTimeout, httpx.ReadTimeout, RetryAfter) as e:
                    retries += 1
                    if retries < MAX_RETRIES:
                        retry_delay = BASE_RETRY_DELAY * retries
                        logger.warning(f"Status update failed with {type(e).__name__} "
                                    f"(attempt {retries}/{MAX_RETRIES}). Retrying in {retry_delay}s")
                        await asyncio.sleep(retry_delay)
                    else:
                        logger.error(f"Status update failed after {MAX_RETRIES} attempts: {str(e)}")
                
        except Exception as e:
            logger.error(f"Error handling task status change: {e}")
            if task.status != TaskStatus.ERROR:
                task.status = TaskStatus.ERROR
                task.error_message = str(e)
                try:
                    await self.message_handler.send_error_message(
                        task.chat_id,
                        task.command_message_id,
                        str(e),
                        self._get_context()
                    )
                except Exception as send_error:
                    logger.error(f"Failed to send error message: {send_error}")
        
        except asyncio.TimeoutError:
            logger.debug(f"Timeout waiting for lock on task {task.task_id}")
            return
        finally:
            if task.task_id in self._task_locks and self._task_locks[task.task_id].locked():
                self._task_locks[task.task_id].release()
            self._task_locks.pop(task.task_id, None)
            
    async def _ensure_status_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Ensure status message exists and is up to date"""
        try:
            if not self.message_handler.status_message_id:
                message = await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="Processing...",
                    reply_to_message_id=update.effective_message.message_id
                )
                self.message_handler.status_message_id = message.message_id
                self.message_handler.status_chat_id = update.effective_chat.id
                
                if self.job_manager:
                    await self.job_manager.start_job(
                        'status_update',
                        lambda: self.message_handler.update_status_message(
                            self.task_queue.get_all_tasks(),
                            context
                        ),
                        self.update_interval
                    )
            
            await self.message_handler.update_status_message(
                self.task_queue.get_all_tasks(),
                context
            )
            
        except Exception as e:
            logger.error(f"Error ensuring status message: {e}")
            raise
            
    async def _create_task_from_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[SubtitleTask]:
        """Create a task from an update"""
        if not (context.args or (update.message.reply_to_message and update.message.reply_to_message.document)):
            raise ValueError("No URL or video file provided")
        
        task_id = self._generate_task_id()
        task = SubtitleTask(
            task_id=task_id,
            chat_id=update.effective_chat.id,
            message_id=update.effective_message.message_id,
            command_message_id=update.effective_message.message_id
        )
        
        if context.args:
            task.url = " ".join(context.args).strip()
        else:
            msg = update.message.reply_to_message
            if not msg.document:
                raise ValueError("Replied message has no video file")
            
            task.file_name = msg.document.file_name or "video.mkv"
            download_task = asyncio.create_task(
                self._download_telegram_file(msg.document.file_id, task.file_name, context)
            )
            task.file_path = await download_task
            
        return task

    async def _download_telegram_file(self, file_id: str, file_name: str, context: ContextTypes.DEFAULT_TYPE) -> str:
        """Download file from Telegram with retry logic"""
        os.makedirs(self.download_dir, exist_ok=True)
        download_path = os.path.join(self.download_dir, file_name)
        
        retries = 0
        while retries < MAX_RETRIES:
            try:
                file = await context.bot.get_file(file_id)
                await file.download_to_drive(download_path)
                return download_path
            except (TimedOut, NetworkError, httpcore.ReadTimeout, httpx.ReadTimeout, RetryAfter) as e:
                retries += 1
                if retries < MAX_RETRIES:
                    retry_delay = BASE_RETRY_DELAY * retries
                    logger.warning(f"Download failed with {type(e).__name__} (attempt {retries}/{MAX_RETRIES}). "
                                 f"Retrying in {retry_delay}s")
                    await asyncio.sleep(retry_delay)
                else:
                    logger.error(f"Download failed after {MAX_RETRIES} attempts with {type(e).__name__}: {str(e)}")
                    raise ValueError(f"Failed to download video after {MAX_RETRIES} attempts: {str(e)}")
            except Exception as e:
                logger.error(f"Unexpected error downloading video: {str(e)}")
                raise ValueError(f"Failed to download video: {str(e)}")
        
    async def _upload_subtitles(self, task: SubtitleTask, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Upload extracted subtitles to Telegram"""
        if not task.output_files:
            raise ValueError("No subtitle files to upload")
            
        uploaded = []
        try:
            for sub_file in task.output_files:
                if os.path.exists(sub_file):
                    retries = 0
                    while retries < MAX_RETRIES:
                        try:
                            with open(sub_file, 'rb') as f:
                                await context.bot.send_document(
                                    chat_id=task.chat_id,
                                    document=f,
                                    filename=os.path.basename(sub_file),
                                    reply_to_message_id=task.command_message_id
                                )
                            uploaded.append(sub_file)
                            break
                        except (TimedOut, NetworkError, httpcore.ReadTimeout, httpx.ReadTimeout, RetryAfter) as e:
                            retries += 1
                            if retries < MAX_RETRIES:
                                retry_delay = BASE_RETRY_DELAY * retries
                                logger.warning(f"Upload failed with {type(e).__name__} (attempt {retries}/{MAX_RETRIES}). "
                                             f"Retrying in {retry_delay}s")
                                await asyncio.sleep(retry_delay)
                            else:
                                logger.error(f"Upload failed after {MAX_RETRIES} attempts with {type(e).__name__}: {str(e)}")
                                raise ValueError(f"Failed to upload subtitle file after {MAX_RETRIES} attempts: {str(e)}")
                else:
                    logger.warning(f"Subtitle file not found: {sub_file}")
        except Exception as e:
            # Only cleanup successfully uploaded files if there's an error
            for sub_file in uploaded:
                try:
                    if os.path.exists(sub_file):
                        os.remove(sub_file)
                except Exception as cleanup_error:
                    logger.warning(f"Failed to clean up subtitle file {sub_file}: {cleanup_error}")
            raise ValueError(f"Failed to upload subtitle files: {str(e)}")
            
    def _cleanup_task_files(self, task: SubtitleTask) -> None:
        """Clean up all files associated with a task"""
        for sub_file in task.output_files:
            if os.path.exists(sub_file):
                try:
                    os.remove(sub_file)
                    logger.info(f"Cleaned up subtitle file: {sub_file}")
                except Exception as e:
                    logger.warning(f"Failed to clean up subtitle file {sub_file}: {e}")
        
        if task.file_path and os.path.exists(task.file_path):
            try:
                os.remove(task.file_path)
                logger.info(f"Cleaned up video file: {task.file_path}")
            except Exception as e:
                logger.warning(f"Failed to clean up video file {task.file_path}: {e}")

    def _get_context(self) -> Optional[ContextTypes.DEFAULT_TYPE]:
        """Get the current bot context from active tasks."""
        for task_data in self.task_processor.active_tasks.values():
            if task_data.get('context'):
                return task_data['context']
        return None

    async def handle_pagination(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle pagination button callbacks"""
        query = update.callback_query
        if query.data.startswith('page_'):
            page = int(query.data.split('_')[1])
            self.message_handler.current_page = page
            await self.message_handler.update_status_message(
                self.task_queue.get_all_tasks(), context)
        await query.answer()
        
    async def handle_log(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /log command to show bot logs"""
        try:
            logs = self.log_buffer.getvalue().splitlines()
            if len(logs) > 50:
                logs = logs[-50:]
            logs = '\n'.join(logs)
            
            if not logs:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="No logs available.",
                    reply_to_message_id=update.effective_message.message_id
                )
                return
                
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("Close", callback_data="close_logs")
            ]])
            
            log_message = f"```\n{logs}\n```"
            
            try:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=log_message,
                    parse_mode='Markdown',
                    reply_to_message_id=update.effective_message.message_id,
                    reply_markup=keyboard
                )
            except Exception as e:
                logger.warning(f"Failed to send full logs, truncating: {e}")
                truncated_logs = f"```\n...truncated...\n{logs[-2000:]}\n```"
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=truncated_logs,
                    parse_mode='Markdown',
                    reply_to_message_id=update.effective_message.message_id,
                    reply_markup=keyboard
                )
                
        except Exception as e:
            logger.error(f"Error handling log command: {e}")
            await self.message_handler.send_error_message(
                update.effective_chat.id,
                update.effective_message.message_id,
                str(e),
                context
            )