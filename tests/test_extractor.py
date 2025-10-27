import os, sys
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.subtitle_extractor import SubtitleExtractor

def test_subtitle_extractor():
    """Test SubtitleExtractor functionality"""
    try:
        sample_video = os.path.join(os.path.dirname(__file__), 'sample_video.mkv')
        if not os.path.exists(sample_video):
            print("✗ Sample video file not found for testing. Please add 'sample_video.mkv' to the test directory.")
            sys.exit(1)
        
        extractor = SubtitleExtractor(sample_video)
        subtitles = extractor.extract_subtitles()
        
        if subtitles:
            print(f"✓ Extracted {len(subtitles)} subtitle(s) successfully")
            for sub in subtitles:
                print(f" - Language: {sub['language']}, Format: {sub['format']}, Path: {sub['path']}")
        else:
            print("✗ No subtitles extracted")
    except Exception as e:
        print(f"✗ SubtitleExtractor error: {e}")
        sys.exit(1)
        
def main():
    print("Running subtitle extraction test...\n")
    test_subtitle_extractor()
    print("\nTest passed!")
    
if __name__ == "__main__":
    main()