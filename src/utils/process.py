import os, sys, signal, logging, asyncio, subprocess
from typing import List, Optional, Callable, Set
from asyncio import Task

logger = logging.getLogger(__name__)

class ProcessRunner:
    """Handles process execution with proper priority and cleanup"""
    
    _tasks: Set[Task]
    nice_level: int
    
    def __init__(self, nice_level: int = 19):
        """Initialize ProcessRunner with task tracking and nice level"""
        self._tasks = set()
        self.nice_level = nice_level
        self._setup_signal_handlers()
        
    def cleanup_tasks(self) -> None:
        """Cancel and cleanup all tracked tasks"""
        for task in self._tasks:
            if not task.done():
                task.cancel()
        self._tasks.clear()
        
    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGBREAK if hasattr(signal, 'SIGBREAK') else signal.SIGTERM):
            try:
                signal.signal(sig, self._handle_signal)
            except ValueError:
                # Signal handlers can only be set in main thread
                pass
                
    def _handle_signal(self, signum, frame):
        """Handle termination signals"""
        logger.info(f"Received signal {signum}, cleaning up tasks...")
        self.cleanup_tasks()
        if signum == signal.SIGINT:
            logger.info("SIGINT received, exiting...")
            sys.exit(0)
        
    def track_task(self, task: Task) -> None:
        """Track an asyncio task for cleanup"""
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        
    async def _set_process_priority(self, process) -> None:
        """Set process priority based on platform"""
        if sys.platform == "win32":
            try:
                import psutil
                p = psutil.Process(process.pid)
                p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
            except ImportError:
                logger.warning("psutil not available, cannot set process priority on Windows")
            except Exception as e:
                logger.warning(f"Failed to set process priority: {e}")
        else:
            if hasattr(os, 'nice'):
                try:
                    os.nice(self.nice_level)
                except Exception as e:
                    logger.warning(f"Failed to set process nice level: {e}")

    async def run_command(self, cmd: List[str], timeout: int = 60, 
                         preexec_fn: Optional[Callable] = None, wait: bool = True) -> any:
        """
        Run a shell command with nice priority and timeout
        
        Args:
            cmd: Command to run as list of strings
            timeout: Command timeout in seconds
            preexec_fn: Function to run in child process before execution
            wait: Whether to wait for command completion
            return_process: Return process object instead of output (implies wait=False)
            
        Returns:
            Command output as string if wait=True
            Process object if wait=False or return_process=True
        """
        process = None
        task = None
        
        try:
            if sys.platform == "win32":
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                await self._set_process_priority(process)
            else:
                default_preexec = lambda: os.nice(self.nice_level) if hasattr(os, 'nice') else None
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    preexec_fn=preexec_fn or default_preexec
                )
            
            if not wait:
                logger.debug(f"Started background process: {' '.join(cmd)}")
                task = asyncio.create_task(self._monitor_process(process, cmd))
                self.track_task(task)
                return process
        
            try:
                task = asyncio.create_task(process.communicate())
                self.track_task(task)
                stdout, stderr = await asyncio.wait_for(task, timeout=timeout)
            except asyncio.TimeoutError:
                if process:
                    process.kill()
                    await process.communicate()
                raise RuntimeError("Command timed out")
            except asyncio.CancelledError:
                logger.info(f"Command cancelled: {' '.join(cmd)}")
                if process:
                    try:
                        process.terminate()
                        try:
                            await asyncio.wait_for(process.wait(), timeout=0.5)
                        except asyncio.TimeoutError:
                            process.kill()
                    except Exception as e:
                        logger.error(f"Error terminating process: {e}")
                raise
            finally:
                if task and not task.done():
                    task.cancel()
            
            if process.returncode != 0:
                stderr_str = stderr.decode(errors="replace").strip()
                if len(stderr_str) > 500:
                    stderr_str = stderr_str[:500] + "..."
                if not stderr_str:
                    stdout_str = stdout.decode(errors="replace").strip()
                    if len(stdout_str) > 500:
                        stdout_str = stdout_str[:500] + "..."
                    if stdout_str:
                        stderr_str = stdout_str
                    else:
                        stderr_str = f"Process failed with exit code {process.returncode}: {' '.join(cmd)}"
                   
                raise RuntimeError(stderr_str)
            
            return stdout.decode(errors="replace").strip()
            
        except Exception as e:
            logger.error(f"Command '{' '.join(cmd)}' failed: {e}")
            if process:
                try:
                    process.kill()
                except Exception:
                    pass
            raise
            
    async def _monitor_process(self, process, cmd: List[str]) -> None:
        """Monitor a background process and cleanup when done"""
        try:
            await process.wait()
            logger.debug(f"Background process completed: {' '.join(cmd)}")
        except asyncio.CancelledError:
            logger.info(f"Background process cancelled: {' '.join(cmd)}")
            try:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    process.kill()
            except Exception as e:
                logger.error(f"Error terminating process: {e}")
        except Exception as e:
            logger.error(f"Error monitoring process: {e}")
            try:
                process.kill()
            except Exception:
                pass
                    
    def cleanup(self) -> None:
        """Cleanup all tasks and resources"""
        self.cleanup_tasks()
        
    async def shutdown(self) -> None:
        """Gracefully shutdown the process runner"""
        self.cleanup()
        # Wait a bit for tasks to clean up
        await asyncio.sleep(0.5)
        
    def __del__(self):
        """Ensure cleanup on garbage collection"""
        self.cleanup()
        
    def __enter__(self):
        """Context manager support"""
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Ensure cleanup on context manager exit"""
        self.cleanup()