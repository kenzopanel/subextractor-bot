import os, sys
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.video_downloader import VideoDownloader

def test_video_downloader():
    """Test VideoDownloader functionality"""
    try:
        download_dir = os.getenv("DOWNLOAD_DIR", "/tmp/downloads")
        if not os.path.exists(download_dir):
            download_dir = os.path.join(download_dir)
            os.makedirs(download_dir, exist_ok=True)
            
        downloader = VideoDownloader(download_dir)
        version = downloader.get_version()
        print(f"✓ VideoDownloader initialized (aria2 version: {version})")
        stats = downloader.get_global_stats()
        print(f"✓ Global stats available: {bool(stats)}")
        print(f"✓ Download directory available: {os.path.exists(download_dir)}")
        
        start_download = downloader.start_download("https://filesamples.com/samples/video/mkv/sample_960x400_ocean_with_audio.mkv")
        if not start_download:
            print("✗ Failed to start download")
            sys.exit(1)
        print(f"✓ Download started with GID: {start_download}")
        
        get_download = downloader.get_download(start_download)
        if not get_download:
            print("✗ Failed to get download info")
            sys.exit(1)
        print(f"✓ Download info retrieved: {get_download.name}, Status: {get_download.status}")
        
        cancel_download = downloader.cancel_download(start_download)
        if not cancel_download:
            print("✗ Failed to cancel download")
            sys.exit(1)
        print(f"✓ Download cancelled successfully")
    except Exception as e:
        print(f"✗ VideoDownloader error: {e}")
        sys.exit(1)
        
def main():
    print("Running video downloader test...\n")
    test_video_downloader()
    print("\nTest passed!")
    
if __name__ == "__main__":
    main()