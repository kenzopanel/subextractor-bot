import os, logging, asyncio, httpcore
from typing import Optional, List
from telegram import Message, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest, TimedOut, RetryAfter, NetworkError
from ..models.task import SubtitleTask
from ..models.task_status import TaskStatus
from ..utils.formatters import MessageFormatter
from ..services.system_stats import SystemStats

MAX_RETRIES = 3 # Maximum number of retries for timeouts
BASE_RETRY_DELAY = 2 # Base delay between retries (will be multiplied by retry count)

logger = logging.getLogger(__name__)

class MessageHandler:
    """Handles Telegram message updates and status messages"""
    
    def __init__(self):
        self.status_message_id: Optional[int] = None
        self.status_chat_id: Optional[int] = None
        self.status_message: Optional[Message] = None
        self.current_page: int = 0
        self.page_size: int = 4
        self.system_stats = SystemStats()
        self.formatter = MessageFormatter()
        self.update_interval = float(os.getenv('UPDATE_INTERVAL', '10.0'))
        self.update_task: Optional[asyncio.Task] = None
        self.last_update: float = 0
        self._context: Optional[ContextTypes.DEFAULT_TYPE] = None
        self._tasks: List[SubtitleTask] = []
        
    def _ensure_update_task(self) -> None:
        """Ensure the update task is running"""
        if not self.update_task or self.update_task.done():
            self.update_task = asyncio.create_task(self._update_loop())

    async def _update_loop(self) -> None:
        """Background task that updates the status message at regular intervals"""
        while self.status_message_id and self.status_chat_id:
            try:
                await self._do_update_status_message()
                await asyncio.sleep(self.update_interval)
            except Exception as e:
                logger.error(f"Error in update loop: {e}")
                await asyncio.sleep(self.update_interval)

    async def _do_update_status_message(self) -> None:
        """Actually perform the status message update"""
        if not self._context or not self.status_message:
            return

        active_tasks = [t for t in self._tasks if t.status not in [TaskStatus.COMPLETED, TaskStatus.ERROR, TaskStatus.CANCELED]]
        
        if not active_tasks:
            try:
                if self.status_message:
                    await self.status_message.delete()
                self.status_message = None
                self.status_message_id = None
                self.status_chat_id = None
                
                if hasattr(self._context, 'application') and hasattr(self._context.application, 'job_manager'):
                    self._context.application.job_manager.stop_job('status_update')
                    
                self._context = None
                self._tasks = []
            except Exception as e:
                logger.warning(f"Failed to delete status message: {e}")
            return
            
        status_text = self._format_status_message(active_tasks)
        
        retries = 0
        while retries < MAX_RETRIES:
            try:
                keyboard = self.create_pagination_keyboard(len(active_tasks))
                
                self.status_message = await self.status_message.edit_text(
                    text=status_text,
                    parse_mode='MarkdownV2',
                    reply_markup=keyboard
                )
                break
                
            except BadRequest as e:
                if "message is not modified" not in str(e).lower():
                    logger.error(f"Error updating status message: {e}")
                break
                
            except (TimedOut, NetworkError, httpcore.ReadTimeout, RetryAfter) as e:
                retries += 1
                if retries < MAX_RETRIES:
                    retry_delay = BASE_RETRY_DELAY * retries
                    logger.warning(f"Network error while updating status message (attempt {retries}/{MAX_RETRIES}). "
                                 f"Retrying in {retry_delay}s")
                    await asyncio.sleep(retry_delay)
                else:
                    logger.error(f"Failed to update status message after {MAX_RETRIES} attempts: {str(e)}")
                    
            except Exception as e:
                logger.error(f"Unexpected error updating status message: {e}")
                break

    async def update_status_message(self, tasks: List[SubtitleTask], context: ContextTypes.DEFAULT_TYPE) -> None:
        """Update the status message with current task states"""
        if not self.status_message_id or not self.status_chat_id:
            return
            
        self._tasks = tasks
        self._context = context
        self._ensure_update_task()
            
    async def send_error_message(self, chat_id: int, message_id: int, error: str, context: Optional[ContextTypes.DEFAULT_TYPE] = None) -> None:
        """Send an error message with retry logic for timeouts"""
        retries = 0
        while retries < MAX_RETRIES:
            try:
                ctx = context or self._context
                if not ctx or not ctx.bot:
                    logger.error("No valid context available to send error message")
                    return
                
                await ctx.bot.send_message(
                    chat_id=chat_id,
                    text=f"Error: {error}",
                    reply_to_message_id=message_id
                )
                break
                
            except (TimedOut, NetworkError, httpcore.ReadTimeout, RetryAfter) as e:
                retries += 1
                if retries < MAX_RETRIES:
                    retry_delay = BASE_RETRY_DELAY * retries
                    logger.warning(f"Network error while sending error message (attempt {retries}/{MAX_RETRIES}). "
                                 f"Retrying in {retry_delay}s")
                    await asyncio.sleep(retry_delay)
                else:
                    logger.error(f"Failed to send error message after {MAX_RETRIES} attempts: {str(e)}")
                    
            except Exception as e:
                logger.error(f"Unexpected error sending error message: {e}")
                break
            
    def create_pagination_keyboard(self, total_tasks: int) -> Optional[InlineKeyboardMarkup]:
        """Create pagination keyboard if needed"""
        total_pages = (total_tasks - 1) // self.page_size + 1
        if total_pages <= 1:
            return None
            
        buttons = []
        if self.current_page > 0:
            buttons.append(InlineKeyboardButton("Prev", callback_data=f"page_{self.current_page - 1}"))
        if self.current_page < total_pages - 1:
            buttons.append(InlineKeyboardButton("Next", callback_data=f"page_{self.current_page + 1}"))
            
        return InlineKeyboardMarkup([buttons]) if buttons else None

    def _format_status_message(self, tasks: list[SubtitleTask]) -> str:
        """Format the status message text"""
        if not tasks: return "No active tasks."
            
        total_tasks = len(tasks)
        total_pages = (total_tasks - 1) // self.page_size + 1
        start_idx = self.current_page * self.page_size
        end_idx = start_idx + self.page_size
        current_tasks = tasks[start_idx:end_idx]
        
        status_texts = []
        for task in current_tasks:
            filename = task.file_path if task.file_path else task.url
            filename = os.path.basename(filename)
            status_text = (
                f"*{self.formatter.escape_markdownv2(filename)}*\n"
                f"{self.formatter.format_progress_bar(task.progress)} "
                f"{self.formatter.escape_markdownv2(f'{task.progress:.2f}')}%\n"
                f"Status: {self.formatter.escape_markdownv2(task.status.title())}\n"
            )
            
            download_size = self.formatter.format_size(task.downloaded)
            total_size = self.formatter.format_size(task.total_size if task.total_size > 0 else 0)
            status_text += (
                f"Downloaded: {self.formatter.escape_markdownv2(download_size)} of "
                f"{self.formatter.escape_markdownv2(total_size)}\n"
            )
            
            if task.status in [TaskStatus.DOWNLOADING, TaskStatus.UPLOADING]:
                speed = self.formatter.format_size(task.speed)
                if task.speed > 0 and task.total_size > task.downloaded:
                    eta = (task.total_size - task.downloaded) / task.speed
                    eta_text = self.formatter.format_time(eta)
                else:
                    eta_text = "∞"
                status_text += (
                    f"Speed: {self.formatter.escape_markdownv2(speed)}/s \\| "
                    f"ETA: {self.formatter.escape_markdownv2(eta_text)}\n"
                )
            
            if task.started_at:
                elapsed = self.formatter.format_time(task.elapsed_time)
                status_text += f"Engine: Aria2c \\| Elapsed: {self.formatter.escape_markdownv2(elapsed)}\n"
            else:
                status_text += "Engine: Aria2c \\| Elapsed: 0s\n"
                
            status_text += f"/cancel\\_{self.formatter.escape_markdownv2(task.task_id)}\n"
            
            status_texts.append(status_text)
            
        if total_pages > 1:
            status_texts.append(
                f"Page {self.formatter.escape_markdownv2(str(self.current_page + 1))}/"
                f"{self.formatter.escape_markdownv2(str(total_pages))}"
            )
            
        total_dl_speed = sum(t.speed for t in tasks if t.status == TaskStatus.DOWNLOADING)
        total_ul_speed = sum(t.speed for t in tasks if t.status == TaskStatus.UPLOADING)
        
        stats = self.system_stats.get_stats()
        bot_stats = (
            f"\n\nBot Stats\n"
            f"CPU: {self.formatter.escape_markdownv2(stats['cpu'])} \\| "
            f"F: {self.formatter.escape_markdownv2(stats['disk'])}\n"
            f"RAM: {self.formatter.escape_markdownv2(stats['ram'])} \\| "
            f"UPTIME: {self.formatter.escape_markdownv2(stats['uptime'])}\n"
            f"DL: {self.formatter.escape_markdownv2(self.formatter.format_size(total_dl_speed))}/s \\| "
            f"UL: {self.formatter.escape_markdownv2(self.formatter.format_size(total_ul_speed))}/s\n"
        )
            
        return "\n\n".join(status_texts) + bot_stats