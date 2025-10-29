import asyncio, logging
from typing import Optional, List, Dict, Callable, Awaitable
from collections import deque
from ..models.task import SubtitleTask
from ..models.task_status import TaskStatus

logger = logging.getLogger(__name__)

class TaskQueue:
    """Manages the subtitle extraction task queue with a single worker"""
    
    def __init__(self):
        self.queue = deque()
        self.active_task: Optional[SubtitleTask] = None
        self.tasks: Dict[str, SubtitleTask] = {}
        self.worker_task: Optional[asyncio.Task] = None
        self.processing = False
        self.task_handlers: Dict[TaskStatus, List[Callable[[SubtitleTask], Awaitable[None]]]] = {
            status: [] for status in TaskStatus
        }
        
    def add_task(self, task: SubtitleTask) -> None:
        """Add a new task to the queue"""
        self.tasks[task.task_id] = task
        self.queue.append(task)
        payload = task.url if task.url else task.file_name
        logger.info(f"Added task {payload} to queue ({task.task_id}). Queue size: {len(self.queue)}")
        self._ensure_worker()
        
    def get_task(self, task_id: str) -> Optional[SubtitleTask]:
        """Get a task by ID"""
        return self.tasks.get(task_id)
        
    def get_all_tasks(self) -> List[SubtitleTask]:
        """Get all tasks"""
        return list(self.tasks.values())
        
    def remove_task(self, task_id: str) -> None:
        """Remove a task from tracking"""
        if task := self.tasks.pop(task_id, None):
            try:
                self.queue.remove(task)
            except ValueError:
                pass
                
    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a specific task"""
        if task := self.get_task(task_id):
            task.status = TaskStatus.CANCELED
            if task is self.active_task:
                if self.worker_task and not self.worker_task.done():
                    self.worker_task.cancel()
                await self._notify_handlers(task)
            else:
                self.remove_task(task_id)
            return True
        return False
        
    async def cancel_all_tasks(self) -> int:
        """Cancel all tasks in queue and current task"""
        count = 0
        if self.active_task:
            await self.cancel_task(self.active_task.task_id)
            count += 1
            
        while self.queue:
            task = self.queue.popleft()
            task.cancel()
            await self._notify_handlers(task)
            count += 1
            
        self.tasks.clear()
        return count
        
    def add_status_handler(self, status: TaskStatus, handler: Callable[[SubtitleTask], Awaitable[None]]) -> None:
        """Add a handler for task status changes"""
        self.task_handlers[status].append(handler)
        
    async def _notify_handlers(self, task: SubtitleTask) -> None:
        """Notify handlers of task status change"""
        handlers = self.task_handlers.get(task.status, [])
        for handler in handlers:
            try:
                await handler(task)
            except Exception as e:
                logger.error(f"Error in task handler: {e}")
                
    def _ensure_worker(self) -> None:
        """Ensure the worker task is running"""
        if not self.worker_task or self.worker_task.done():
            self.worker_task = asyncio.create_task(self._process_queue())
            
    async def _process_queue(self) -> None:
        """Process tasks in the queue"""
        while self.queue:
            if self.active_task:
                await asyncio.sleep(1)
                continue
                
            self.active_task = self.queue.popleft()
            self.active_task.start()
            
            try:
                await self._notify_handlers(self.active_task)
                while self.active_task.status not in [
                    TaskStatus.COMPLETED, 
                    TaskStatus.ERROR, 
                    TaskStatus.CANCELED
                ]:
                    await asyncio.sleep(1)
                    
            except Exception as e:
                logger.error(f"Error processing task {self.active_task.task_id}: {e}")
                self.active_task.fail(str(e))
                
            finally:
                await self._notify_handlers(self.active_task)
                self.remove_task(self.active_task.task_id)
                self.active_task = None