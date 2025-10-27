import os, time, aria2p, validators
from typing import Optional
from urllib.parse import unquote, urlparse


class VideoDownloader:
    """Wrapper around aria2p for starting and managing downloads via aria2 RPC."""

    def __init__(self, download_dir: str, host: str = "http://localhost", port: int = 6800, secret: str = ""):
        self.download_dir = download_dir
        self.aria2 = aria2p.API(aria2p.Client(host=host, port=port, secret=secret))

    def start_download(self, url: str, out_filename: Optional[str] = None) -> Optional[str]:
        """Start a download and return the aria2 GID (string) or None on failure."""
        try:
            if not validators.url(url):
                raise ValueError(f"Invalid URL: {url}")

            parsed = urlparse(url)
            filename = out_filename or os.path.basename(parsed.path) or f"video_{int(time.time())}.mkv"
            if not filename.lower().endswith('.mkv'):
                filename = filename + '.mkv'

            filename = unquote(filename)
            os.makedirs(self.download_dir, exist_ok=True)
            download = self.aria2.add_uris([url], {'dir': self.download_dir, 'out': filename})
            
            if not download:
                raise RuntimeError("Failed to start download")
                
            # Set start time as Unix timestamp
            download.start_time = time.time()
            
            return download.gid
        except Exception as e:
            print(f"VideoDownloader.start_download error: {e}")
            return None

    def get_download(self, gid: str) -> Optional[aria2p.Download]:
        """Return an aria2p.Download object for the given gid (or None)."""
        try:
            dl = self.aria2.get_download(gid)
            dl.update()
            return dl
        except Exception:
            return None

    def cancel_download(self, gid: str) -> bool:
        """Cancel (remove) the download and return True if succeeded."""
        try:
            download = self.get_download(gid)
            if download:
                return download.remove(force=True, files=True)
            return True  # Already gone
        except Exception:
            return False

    def cancel(self, gid: str) -> bool:
        """Alias for cancel_download for compatibility."""
        return self.cancel_download(gid)

    def cleanup(self):
        """Cancel all downloads and clean up any temporary files."""
        try:
            # Remove all downloads
            downloads = self.aria2.get_downloads()
            for download in downloads:
                try:
                    download.remove(force=True, files=True)
                except Exception as e:
                    print(f"Error removing download {download.gid}: {e}")
        except Exception as e:
            print(f"Error in cleanup: {e}")
        
        # Try to clean up the download directory
        try:
            if os.path.exists(self.download_dir):
                for filename in os.listdir(self.download_dir):
                    filepath = os.path.join(self.download_dir, filename)
                    try:
                        if os.path.isfile(filepath):
                            os.remove(filepath)
                    except Exception as e:
                        print(f"Error removing file {filepath}: {e}")
        except Exception as e:
            print(f"Error cleaning download directory: {e}")

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