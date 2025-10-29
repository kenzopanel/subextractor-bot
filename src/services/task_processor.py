import os, logging, asyncio
from typing import Dict, Any
from telegram.ext import ContextTypes
from .video_downloader import VideoDownloader
from .subtitle_processor import SubtitleProcessor
from ..models.task import SubtitleTask
from ..models.task_status import TaskStatus

logger = logging.getLogger(__name__)

class TaskProcessor:
    """Handles the processing of subtitle extraction tasks"""
    
    def __init__(self, download_dir: str, aria2_service: Any):
        self.download_dir = download_dir
        self.video_downloader = VideoDownloader(download_dir, aria2_service)
        self.subtitle_processor = SubtitleProcessor(download_dir, self.video_downloader)
        self.active_tasks: Dict[str, Dict[str, Any]] = {}

    async def process_task(self, task: SubtitleTask, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Process a single subtitle extraction task"""
        try:
            self.active_tasks[task.task_id] = {"task": task, "context": context}
            task.start()
            
            if task.url:
                await self._handle_url_download(task)
            elif task.file_path:
                await self._handle_local_file(task)
            else:
                raise ValueError("No URL or video file provided")
                
            task.complete(TaskStatus.COMPLETED)
            
        except asyncio.CancelledError:
            logger.info(f"Task {task.task_id} was cancelled")
            await self._cleanup_task(task)
            task.cancel()
            self.active_tasks.pop(task.task_id, None)
            
        except Exception as e:
            logger.error(f"Error processing task {task.task_id}: {e}")
            
            error_msg = str(e)
            if "No subtitles found" in error_msg:
                user_msg = "No subtitles were found in this video file."
            elif "No URL or video file" in error_msg:
                user_msg = "Please provide a video file or URL."
            else:
                user_msg = f"Error processing video: {error_msg}"
            
            await self._cleanup_task(task)
            task.fail(user_msg)
    
    async def _handle_url_download(self, task: SubtitleTask) -> None:
        """Handle downloading video from URL"""
        task.status = TaskStatus.DOWNLOADING
        gid = self.video_downloader.start_download(task.url)
        if not gid:
            raise RuntimeError("Failed to start download")
            
        task.gid = gid
        
        while True:
            download = self.video_downloader.get_download(gid)
            if not download:
                raise RuntimeError("Download not found")
                
            if download.has_failed:
                raise RuntimeError(f"Download failed: {download.error_message}")
                
            if download.is_complete:
                task.file_path = os.path.join(self.download_dir, download.name)
                break
                
            download.update()
            downloaded = download.completed_length
            total = download.total_length
            speed = download.download_speed
            
            if total > 0:
                progress = (downloaded / total * 100)
            else:
                progress = float(download.progress) if download.progress else 0
                
            if download.has_failed:
                raise RuntimeError(f"Download failed: {download.error_message}")
            
            task.update_progress(progress, speed, downloaded, total)
            await asyncio.sleep(1)
            
        await self._handle_local_file(task)
    
    async def _handle_local_file(self, task: SubtitleTask) -> None:
        """Handle processing a local video file"""
        task.status = TaskStatus.EXTRACTING
        extraction_task = None
        
        try:
            extraction_task = asyncio.create_task(
                self.subtitle_processor.extract_subtitles(task.file_path, task)
            )
            subtitles = await extraction_task
            
            if task.status == TaskStatus.CANCELED:
                raise asyncio.CancelledError("Task was canceled during extraction")
                
            if not subtitles:
                raise RuntimeError("No subtitles found in video file")
                
            task.status = TaskStatus.UPLOADING
            task.metadata['subtitles'] = subtitles
            
        except asyncio.CancelledError:
            if extraction_task and not extraction_task.done():
                extraction_task.cancel()
                try:
                    await extraction_task
                except asyncio.CancelledError:
                    pass
            raise
            
        finally:
            if task.gid and os.path.exists(task.file_path):
                try:
                    os.remove(task.file_path)
                except Exception as e:
                    logger.warning(f"Failed to remove file {task.file_path}: {e}")
    
    async def _cleanup_task(self, task: SubtitleTask) -> None:
        """Clean up task resources"""
        try:
            if task.gid:
                self.video_downloader.cancel(task.gid)
                
            if task.file_path and os.path.exists(task.file_path):
                try:
                    os.remove(task.file_path)
                except Exception as e:
                    logger.warning(f"Failed to remove video file {task.file_path}: {e}")
                    
            if task.output_files:
                for file_path in task.output_files:
                    try:
                        if os.path.exists(file_path):
                            os.remove(file_path)
                    except Exception as e:
                        logger.warning(f"Failed to remove output file {file_path}: {e}")
                        
        except Exception as e:
            logger.error(f"Error during task cleanup: {e}")
    
    def cancel_task(self, task_id: str) -> bool:
        """Cancel a specific task"""
        if task_data := self.active_tasks.get(task_id):
            task = task_data["task"]
            if task.gid:
                self.video_downloader.cancel(task.gid)
            task.cancel()
            return True
        return False
    
    def cleanup(self) -> None:
        """Clean up all tasks and resources"""
        self.video_downloader.cleanup()
        for task_data in self.active_tasks.values():
            task_data["task"].cancel()
        self.active_tasks.clear()