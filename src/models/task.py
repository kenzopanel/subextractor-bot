from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any
from .task_status import TaskStatus

@dataclass
class SubtitleTask:
    """Represents a subtitle extraction task"""
    task_id: str
    chat_id: int
    message_id: int
    command_message_id: int
    file_path: Optional[str] = None
    url: Optional[str] = None
    gid: Optional[str] = None
    status: TaskStatus = TaskStatus.WAITING
    progress: float = 0.0
    speed: int = 0
    downloaded: int = 0
    total_size: int = 0
    error_message: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    output_files: list = field(default_factory=list)
    
    def update_progress(self, progress: float, speed: int = 0, 
                       downloaded: int = None, total: int = None) -> None:
        """Update task progress information"""
        self.progress = progress
        self.speed = speed
        if downloaded is not None:
            self.downloaded = downloaded
        if total is not None:
            self.total_size = total
            
    def start(self) -> None:
        """Mark task as started"""
        self.started_at = datetime.now()
        
    def complete(self, status: TaskStatus = TaskStatus.COMPLETED) -> None:
        """Mark task as completed with given status"""
        self.status = status
        self.completed_at = datetime.now()
        if status == TaskStatus.COMPLETED:
            self.progress = 100.0
            
    def fail(self, error_message: str) -> None:
        """Mark task as failed with error message"""
        self.status = TaskStatus.ERROR
        self.error_message = error_message
        self.completed_at = datetime.now()
        
    def cancel(self) -> None:
        """Mark task as canceled"""
        self.status = TaskStatus.CANCELED
        self.completed_at = datetime.now()
        
    @property
    def elapsed_time(self) -> float:
        """Get elapsed time in seconds"""
        if not self.started_at:
            return 0
        end = self.completed_at or datetime.now()
        return (end - self.started_at).total_seconds()