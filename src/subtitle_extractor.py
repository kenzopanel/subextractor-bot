import os
import tempfile
from typing import List, Dict, Optional
import ffmpeg
import pysrt
import ass
from pymkv import MKVFile

class SubtitleExtractor:
    def __init__(self, video_path: str):
        self.video_path = video_path
        self.temp_dir = tempfile.mkdtemp()
        
    def extract_subtitles(self) -> List[Dict[str, str]]:
        """
        Extract all subtitles from the video file.
        Returns a list of dictionaries containing subtitle info:
        [{"language": "eng", "format": "srt", "path": "/path/to/subtitle.srt"}]
        """
        mkv = MKVFile(self.video_path)
        subtitle_tracks = []
        
        # Get all subtitle tracks
        for track in mkv.tracks:
            if track.track_type == "subtitles":
                subtitle_info = {
                    "language": track.language or "und",
                    "format": track.track_codec.lower(),
                    "track_id": track.track_id
                }
                subtitle_tracks.append(subtitle_info)
        
        extracted_subtitles = []
        
        for track in subtitle_tracks:
            output_path = os.path.join(
                self.temp_dir,
                f"subtitle_{track['language']}_{track['track_id']}.{track['format']}"
            )
            
            try:
                # Extract subtitle using ffmpeg
                stream = ffmpeg.input(self.video_path)
                stream = ffmpeg.output(
                    stream,
                    output_path,
                    map=f"0:{track['track_id']}",
                    c="copy"
                )
                ffmpeg.run(stream, overwrite_output=True, quiet=True)
                
                track["path"] = output_path
                extracted_subtitles.append(track)
            except ffmpeg.Error:
                continue
                
        return extracted_subtitles
    
    def cleanup(self):
        """Remove temporary files"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)