import os
import logging
import psutil
import aria2p
import asyncio
import urllib3
import requests
from typing import Optional, Set
from ..utils.process import ProcessRunner

logger = logging.getLogger(__name__)

class Aria2Service:
    """Manages aria2c daemon and RPC client with proper process management"""
    
    def __init__(self, host: str = "http://localhost", port: int = 6800, secret: str = "", 
                 config_path: Optional[str] = None, nice_level: int = 19):
        self.host = host
        self.port = port
        self.secret = secret
        self.config_path = config_path
        self.process_runner = ProcessRunner(nice_level)
        self.client: Optional[aria2p.API] = None
        self._process = None
        self._child_pids: Set[int] = set()
        
    def _find_aria2c_processes(self) -> Set[int]:
        """Find all running aria2c processes"""
        try:
            pids = set()
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    if proc.info['name'] == 'aria2c':
                        pids.add(proc.info['pid'])
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return pids
        except Exception as e:
            logger.warning(f"Failed to find aria2c processes: {e}")
            return set()

    def _kill_existing_aria2c(self) -> None:
        """Kill any existing aria2c processes"""
        pids = self._find_aria2c_processes()
        for pid in pids:
            try:
                process = psutil.Process(pid)
                process.terminate()
                try:
                    process.wait(timeout=3)
                except psutil.TimeoutExpired:
                    process.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            except Exception as e:
                logger.warning(f"Failed to kill aria2c process {pid}: {e}")

    async def start(self) -> bool:
        """Start aria2c daemon with proper configuration
        
        Returns:
            bool: True if successfully started and client initialized, False otherwise
        """
        try:
            logger.debug("Killing existing aria2c processes...")
            self._kill_existing_aria2c()
            
            if not self.config_path:
                self.config_path = os.path.abspath(
                    os.path.join(os.path.dirname(__file__), '..', '..', "aria2.conf")
                )
            logger.debug(f"Using config path: {self.config_path}")
            
            cmd = ["aria2c", "--enable-rpc", f"--conf-path={self.config_path}"]
            logger.debug(f"Starting aria2c with command: {' '.join(cmd)}")
            
            proc = await self.process_runner.run_command(cmd, timeout=10, wait=False)
            if not proc:
                logger.error("Failed to start aria2c process")
                return False
            
            logger.debug(f"Aria2c process started with PID: {proc.pid}")
            self._process = proc
            
            await asyncio.sleep(0.5)
            
            try:
                parent = psutil.Process(proc.pid)
                children = parent.children(recursive=True)
                self._child_pids = {child.pid for child in children}
                self._child_pids.add(proc.pid)
            except Exception as e:
                logger.warning(f"Failed to store child process IDs: {e}")
            
            retries = 5
            while retries > 0:
                try:
                    logger.debug(f"Attempting to connect to aria2 at {self.host}:{self.port}")
                    client = aria2p.Client(host=self.host, port=self.port, secret=self.secret)
                    self.client = aria2p.API(client)
                    
                    # Test connection
                    version = self.client.client.get_version()
                    if not version:
                        logger.error("Failed to get aria2c version")
                        return False
                    
                    logger.info(f"Successfully connected to aria2 {version['version']}")
                    return True
                except requests.exceptions.ConnectionError as e:
                    logger.debug(f"Connection failed (attempt {6-retries}/5): {e}")
                except Exception as e:
                    logger.debug(f"Unexpected error during connection (attempt {6-retries}/5): {e}")
                    
                except (requests.ConnectionError, urllib3.exceptions.ConnectionError):
                    retries -= 1
                    if retries > 0:
                        await asyncio.sleep(1)
                    continue
                except Exception as e:
                    logger.error(f"Failed to initialize aria2 client: {e}")
                    return None
                    
            logger.error("Failed to connect to aria2 after retries")
            return None
            
        except Exception as e:
            logger.error(f"Failed to initialize aria2c: {e}")
            self.stop()
            return None
            
    def stop(self) -> None:
        """Stop aria2c daemon and cleanup"""
        logger.info("Stopping aria2c service")
        if self.client:
            try:
                downloads = self.client.get_downloads()
                for download in downloads:
                    try:
                        download.remove(force=True, files=True)
                    except Exception:
                        pass
            except Exception:
                pass
        
        for pid in self._child_pids:
            try:
                process = psutil.Process(pid)
                process.terminate()
                try:
                    process.wait(timeout=3)
                except psutil.TimeoutExpired:
                    process.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            except Exception as e:
                logger.debug(f"Error terminating process {pid}: {e}")
        
        self._kill_existing_aria2c()
        self._process = None
        self._child_pids.clear()
        self.client = None
                
    def is_alive(self) -> bool:
        """Check if aria2c daemon is running and responsive"""
        try:
            if not self.client:
                logger.debug("No client available")
                return False

            if self._process and self._process.returncode is not None:
                logger.debug(f"Process has exited with code: {self._process.returncode}")
                return False
            
            try:
                version = self.client.client.get_version()
                logger.debug(f"API connection successful, version: {version}")
                return True
            except Exception as e:
                logger.debug(f"API connection test failed: {e}")
                return False
                
        except Exception as e:
            logger.debug(f"Error checking service status: {e}")
            return False
        
    def get_client(self) -> Optional[aria2p.API]:
        """Get the aria2p API client if service is running"""
        if not self.client:
            logger.debug("No client available")
            return None
            
        try:
            if self.is_alive():
                logger.debug("Service is alive, returning client")
                return self.client
            
            logger.debug("Service is not alive")
            return None
        except Exception as e:
            logger.debug(f"Error checking client: {e}")
            return None
        except Exception as e:
            logger.error(f"Error getting aria2 client: {e}")
            return None
        
    def __enter__(self):
        """Context manager support"""
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Ensure proper cleanup on exit"""
        try:
            self.stop()
        except Exception as e:
            logger.debug(f"Error during cleanup: {e}")
            
    def __del__(self):
        """Ensure cleanup on garbage collection"""
        try:
            self.stop()
        except Exception:
            pass