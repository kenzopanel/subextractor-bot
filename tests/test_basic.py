import os, sys
from dotenv import load_dotenv

load_dotenv()

# Add the project root to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.bot import SubtitleBot
from src.video_downloader import VideoDownloader
from src.system_stats import SystemStats

def test_imports():
    """Test that all required modules can be imported"""
    try:
        import telegram
        import aria2p
        import psutil
        import ffmpeg
        import pysrt
        import ass
        print("✓ All external dependencies imported successfully")
    except ImportError as e:
        print(f"✗ Import error: {e}")
        sys.exit(1)

def test_video_downloader():
    """Test VideoDownloader initialization and basic methods"""
    try:
        download_dir = os.getenv("TEMP_DIR", "/downloads")
        if not os.path.exists(download_dir):
            download_dir = os.path.join(download_dir)
            os.makedirs(download_dir, exist_ok=True)
            
        downloader = VideoDownloader(download_dir)
        version = downloader.get_version()
        stats = downloader.get_global_stats()
        print(f"✓ VideoDownloader initialized (aria2 version: {version})")
        print(f"✓ Global stats available: {bool(stats)}")
        print(f"✓ Download directory available: {os.path.exists(download_dir)}")
    except Exception as e:
        print(f"✗ VideoDownloader error: {e}")
        sys.exit(1)

def test_system_stats():
    """Test SystemStats collection"""
    try:
        stats = SystemStats()
        metrics = stats.get_stats()
        print(f"✓ System stats collected: CPU={metrics['cpu']}, RAM={metrics['ram']}")
    except Exception as e:
        print(f"✗ SystemStats error: {e}")
        sys.exit(1)

def main():
    print("Running basic validation tests...\n")
    
    # Test imports
    test_imports()
    
    # Test core components
    test_video_downloader()
    test_system_stats()
    
    print("\nAll tests passed!")

if __name__ == "__main__":
    main()