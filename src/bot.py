import os, sys, logging, asyncio
from dotenv import load_dotenv
from telegram.ext import (
    Application,
    CommandHandler as TelegramCommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters
)
from .services.task_queue import TaskQueue
from .services.job_manager import JobManager
from .services.aria2_service import Aria2Service
from .handlers.command_handler import CommandHandler
from .models.task_status import TaskStatus
from .utils.logging_config import configure_logging

load_dotenv()
log_buffer = configure_logging(log_level=os.getenv('LOG_LEVEL', 'INFO'))
logger = logging.getLogger(__name__)

class SubtitleBot:
    """Main bot class coordinating all components"""
    
    def __init__(self):
        self.token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.download_dir = os.getenv('DOWNLOAD_DIR', '/tmp/downloads')
        os.makedirs(self.download_dir, exist_ok=True)
        
        self.task_queue = TaskQueue()
        self.aria2_service = None
        self.command_handler = None
        
    async def init_bot(self):
        """Initialize the bot and set up handlers"""
        application = (
            Application.builder()
            .token(self.token)
            .concurrent_updates(True)
            .build()
        )
        
        await application.initialize()
        await application.bot.initialize()
        
        self.aria2_service = Aria2Service()
        if not await self.aria2_service.start():
            logger.error("Failed to start aria2c service")
            sys.exit(1)
            
        await asyncio.sleep(1)
        
        client = self.aria2_service.get_client()
        if not client:
            logger.error("Aria2 client initialization failed")
            sys.exit(1)
            
        logger.info("Aria2 service successfully initialized")
        self.command_handler = CommandHandler(self.task_queue, self.aria2_service)
        
        for status in TaskStatus:
            self.task_queue.add_status_handler(
                status,
                self.command_handler.handle_task_status_change
            )
        
        self.job_manager = JobManager(application)
        self.command_handler.set_job_manager(self.job_manager)
        
        application.add_handler(TelegramCommandHandler("start", self.command_handler.start))
        application.add_handler(TelegramCommandHandler("help", self.command_handler.help))
        application.add_handler(TelegramCommandHandler(["extract", "e"], self.command_handler.handle_extract))
        application.add_handler(TelegramCommandHandler("status", self.command_handler.handle_status))
        application.add_handler(TelegramCommandHandler("cancelall", self.command_handler.handle_cancelall))
        application.add_handler(TelegramCommandHandler("log", self.command_handler.handle_log))
        
        application.add_handler(MessageHandler(
            filters.Regex(r'^/cancel_([a-zA-Z0-9-]+)$'),
            self.command_handler.handle_cancel
        ))
        
        application.add_handler(MessageHandler(
            (filters.Document.VIDEO | filters.Document.ALL) & 
            filters.Caption('^/extract') & ~filters.COMMAND,
            self.command_handler.handle_extract
        ))
        
        application.add_handler(CallbackQueryHandler(
            self.command_handler.handle_page_callback,
            pattern="^page_"
        ))
        
        application.add_handler(CallbackQueryHandler(
            self.command_handler.handle_close_logs,
            pattern="^close_logs$"
        ))
        
        return application
        
    def run(self):
        """Start the bot"""
        try:
            if sys.platform != "win32":
                try:
                    import uvloop
                    uvloop.install()
                    logger.info("Using uvloop for improved performance")
                except ImportError:
                    logger.warning("uvloop not available, using default event loop")
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                app = loop.run_until_complete(self.init_bot())
                logger.info("Starting bot...")
                app.run_polling()
            finally:
                # Clean up resources
                if hasattr(self.command_handler, 'task_processor'):
                    self.command_handler.task_processor.cleanup()
                if hasattr(self, 'aria2_service'):
                    self.aria2_service.stop()
                loop.close()
                
        except Exception as e:
            logger.error(f"Bot crashed: {e}", exc_info=True)
            sys.exit(1)