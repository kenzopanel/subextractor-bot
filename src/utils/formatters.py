import datetime, logging
from typing import Union

logger = logging.getLogger(__name__)

class MessageFormatter:
    """Utility class for formatting messages and values"""
    
    @staticmethod
    def format_size(size_bytes: float) -> str:
        """Format bytes to human readable string."""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024:
                return f"{size_bytes:.2f}{unit}"
            size_bytes /= 1024
        return f"{size_bytes:.2f}TB"

    @staticmethod
    def format_progress_bar(percentage: float, width: int = 12) -> str:
        """Create a progress bar string."""
        filled = int(width * percentage / 100)
        return f"{'▧' * filled}{'□' * (width - filled)}"

    @staticmethod
    def format_time(time_value: Union[float, int, datetime.timedelta]) -> str:
        """Format time value to MM:SS or HH:MM:SS."""
        try:
            if time_value is None:
                return "∞"
                
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
                
        except Exception as e:
            logger.warning(f"Error formatting time value {time_value}: {e}")
            return "∞"

    @staticmethod
    def escape_markdownv2(text: str) -> str:
        """Escape special characters for Telegram MarkdownV2 format."""
        SPECIAL_CHARACTERS = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', 
                            '-', '=', '|', '{', '}', '.', '!']
        text = str(text)
        
        for char in SPECIAL_CHARACTERS:
            text = text.replace(char, f'\\{char}')
            
        return text