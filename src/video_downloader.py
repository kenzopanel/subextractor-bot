import os
import time
import asyncio
from typing import Optional
import aria2p
import validators
from urllib.parse import urlparse


class VideoDownloader:
    """Wrapper around aria2p for starting and managing downloads via aria2 RPC.

    Assumes an aria2 RPC server is available (aria2c --enable-rpc ...).
    """

    def __init__(self, temp_dir: str, host: str = "http://localhost", port: int = 6800, secret: str = ""):
        self.temp_dir = temp_dir
        self.aria2 = aria2p.API(aria2p.Client(host=host, port=port, secret=secret))

    def start_download(self, url: str, out_filename: Optional[str] = None) -> Optional[str]:
        """Start a download and return the aria2 GID (string) or None on failure."""
        try:
            if not validators.url(url):
                return None

            parsed = urlparse(url)
            filename = out_filename or os.path.basename(parsed.path) or "video.mkv"
            if not filename.lower().endswith('.mkv'):
                # force mkv extension if not present
                filename = filename + '.mkv'

            # Ensure temp dir exists
            os.makedirs(self.temp_dir, exist_ok=True)

            download = self.aria2.add_uris([url], {'dir': self.temp_dir, 'out': filename})
            # aria2p returns a Download object
            return download.gid
        except Exception as e:
            print(f"VideoDownloader.start_download error: {e}")
            return None

    def get_download(self, gid: str) -> Optional[aria2p.Download]:
        """Return an aria2p.Download object for the given gid (or None)."""
        try:
            dl = self.aria2.get_download(gid)
            # Refresh info
            dl.update()
            return dl
        except Exception:
            return None

    def cancel_download(self, gid: str) -> bool:
        """Cancel (remove) the download and return True if succeeded."""
        try:
            dl = self.aria2.get_download(gid)
            # remove the download and delete file
            self.aria2.remove([dl])
            return True
        except Exception:
            return False

    def get_global_stats(self) -> dict:
        """Return aria2 global stats (download/upload speed etc.) as dict."""
        try:
            stats = self.aria2.get_global_stat()
            return stats
        except Exception:
            return {}

    def get_version(self) -> str:
        """Return aria2 version string if available."""
        try:
            return self.aria2.client.get_version()
        except Exception:
            return "aria2"