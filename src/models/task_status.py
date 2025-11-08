from enum import Enum, auto

class TaskStatus(Enum):
    """Task processing status enum"""
    WAITING = auto()      # Task is in queue waiting to be processed
    DOWNLOADING = auto()  # Currently downloading the video
    EXTRACTING = auto()   # Extracting subtitles from video
    UPLOADING = auto()    # Uploading subtitles to Telegram
    COMPLETED = auto()    # Task completed successfully
    ERROR = auto()        # Task failed with error
    CANCELED = auto()     # Task was canceled by user
    
    def title(self) -> str:
        """Get a display-friendly title for the status"""
        return self.name.title()