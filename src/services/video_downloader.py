import os
import time
import logging
import validators
from typing import Optional
from urllib.parse import unquote, urlparse
from .aria2_service import Aria2Service

logger = logging.getLogger(__name__)

class VideoDownloader:
    """Manages video downloads using aria2c with proper service management"""

    def __init__(self, download_dir: str, aria2_service: Aria2Service):
        self.download_dir = download_dir
        self.aria2_service = aria2_service

    def __exit__(self):
        self.cleanup()

    def start_download(self, url: str, out_filename: Optional[str] = None) -> Optional[str]:
        """Start a download and return the aria2 GID (string) or None on failure"""
        try:
            client = self.aria2_service.get_client()
            if not client:
                raise RuntimeError("Aria2c daemon is not running")

            if not validators.url(url):
                raise ValueError(f"Invalid URL: {url}")

            parsed = urlparse(url)
            filename = out_filename or os.path.basename(parsed.path) or f"video_{int(time.time())}.mkv"
            if not filename.lower().endswith('.mkv'):
                filename = filename + '.mkv'

            filename = unquote(filename)
            os.makedirs(self.download_dir, exist_ok=True)
            download = client.add_uris(
                [url], 
                {'dir': self.download_dir, 'out': filename}
            )
            
            time.sleep(0.5)
            if not download:
                raise RuntimeError("Failed to start download")

            time.sleep(1)
            download.update()
            if getattr(download, 'status', None) == 'error' or getattr(download, 'error_message', None):
                raise RuntimeError(f"Download failed to start: {getattr(download, 'error_message', 'Unknown error')}")
            if download.has_failed:
                raise RuntimeError(f"Download failed to start: {download.error_message}")

            if getattr(download, 'status', None) == 'complete':
                file_path = os.path.join(self.download_dir, download.name)
                if not os.path.exists(file_path):
                    raise RuntimeError(f"Download complete but file does not exist: {file_path}")
                min_size = 1024 * 1024  # 1MB
                actual_size = os.path.getsize(file_path)
                if actual_size < min_size:
                    raise RuntimeError(f"Downloaded file is too small: {file_path}")

            return download.gid
        except Exception as e:
            logger.error(f"Failed to start download: {e}")
            return None

    def get_download(self, gid: str) -> Optional[object]:
        """Return a download object for the given gid (or None)"""
        try:
            client = self.aria2_service.get_client()
            if not client:
                return None
                
            dl = client.get_download(gid)
            dl.update()
            return dl
        except Exception:
            return None

    def cancel_download(self, gid: str) -> bool:
        """Cancel (remove) the download and return True if succeeded"""
        try:
            download = self.get_download(gid)
            if download:
                return download.remove(force=True, files=True)
            return True  # Already gone
        except Exception:
            return False

    def cancel(self, gid: str) -> bool:
        """Alias for cancel_download for compatibility"""
        return self.cancel_download(gid)

    def cleanup(self):
        """Cancel all downloads and clean up any temporary files"""
        client = self.aria2_service.get_client()
        if client:
            try:
                downloads = client.get_downloads()
                for download in downloads:
                    try:
                        download.remove(force=True, files=True)
                    except Exception as e:
                        logger.error(f"Error removing download {download.gid}: {e}")
            except Exception as e:
                logger.error(f"Error in cleanup: {e}")
        
        try:
            if os.path.exists(self.download_dir):
                for filename in os.listdir(self.download_dir):
                    filepath = os.path.join(self.download_dir, filename)
                    try:
                        if os.path.isfile(filepath):
                            os.remove(filepath)
                    except Exception as e:
                        logger.error(f"Error removing file {filepath}: {e}")
        except Exception as e:
            logger.error(f"Error cleaning download directory: {e}")

    def get_global_stats(self) -> dict:
        """Return aria2 global stats (download/upload speed etc.) as dict"""
        try:
            client = self.aria2_service.get_client()
            if client:
                return client.get_global_stat()
        except Exception:
            pass
        return {}