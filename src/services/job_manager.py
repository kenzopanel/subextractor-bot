import logging, asyncio
from typing import Callable, Dict
from telegram.ext import Application

logger = logging.getLogger(__name__)

class JobManager:
    """Manages periodic jobs and their scheduling"""
    
    def __init__(self, application: Application):
        self.application = application
        self.jobs: Dict[str, asyncio.Task] = {}
        self.callbacks: Dict[str, Callable] = {}
        self.intervals: Dict[str, float] = {}
        
    async def start_job(self, job_id: str, callback: Callable, interval: float) -> None:
        """Start a new periodic job"""
        if job_id in self.jobs and not self.jobs[job_id].done():
            return
            
        self.callbacks[job_id] = callback
        self.intervals[job_id] = interval
        self.jobs[job_id] = asyncio.create_task(self._run_job(job_id))
        
    def stop_job(self, job_id: str) -> None:
        """Stop a running job"""
        if job_id in self.jobs and not self.jobs[job_id].done():
            self.jobs[job_id].cancel()
            
    def stop_all_jobs(self) -> None:
        """Stop all running jobs"""
        for job_id in list(self.jobs.keys()):
            self.stop_job(job_id)
            
    async def _run_job(self, job_id: str) -> None:
        """Run a job periodically"""
        try:
            while True:
                await self.callbacks[job_id]()
                await asyncio.sleep(self.intervals[job_id])
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in job {job_id}: {e}")
        finally:
            self.jobs.pop(job_id, None)
            self.callbacks.pop(job_id, None)
            self.intervals.pop(job_id, None)