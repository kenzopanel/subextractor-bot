import sys, logging
from io import StringIO

class LimitedStringIO(StringIO):
    """StringIO buffer with size limit to prevent memory issues"""
    def __init__(self, max_size: int = 50_000):
        super().__init__()
        self.max_size = max_size

    def write(self, s: str) -> int:
        """Write string to buffer, truncating if size exceeds max_size"""
        result = super().write(s)
        if self.tell() > self.max_size:
            content = self.getvalue()
            keep_size = self.max_size // 2
            self.seek(0)
            self.truncate()
            self.write(content[-keep_size:])
        return result

def configure_logging(log_level: str = "DEBUG") -> LimitedStringIO:
    """Configure centralized logging for the application
    
    Args:
        log_level: The logging level to use
        
    Returns:
        LimitedStringIO buffer containing log output with 50K size limit
    """
    log_buffer = LimitedStringIO()
    
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level))
    
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    buffer_handler = logging.StreamHandler(log_buffer)
    buffer_handler.setFormatter(formatter)
    root_logger.addHandler(buffer_handler)
    
    configure_module_loggers()
    
    old_error = logging.Logger.error

    def patched_error(self, msg, *args, **kwargs):
        # kwargs.setdefault("exc_info", True)
        return old_error(self, msg, *args, **kwargs)

    logging.Logger.error = patched_error
    
    return log_buffer

def configure_module_loggers() -> None:
    """Configure specific logging settings for different modules"""
    app_logger = logging.getLogger("subtitle_extractor_bot")
    app_logger.setLevel(logging.INFO)
    
    modules = {
        "subtitle_extractor_bot.services": logging.INFO,
        "subtitle_extractor_bot.handlers": logging.INFO,
        "subtitle_extractor_bot.models": logging.INFO,
        "httpx": logging.WARNING,
        "telegram": logging.INFO,
        # "aria2p": logging.INFO
    }
    
    for module, level in modules.items():
        logger = logging.getLogger(module)
        logger.setLevel(level)