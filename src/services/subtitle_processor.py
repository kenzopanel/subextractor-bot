import os
import sys
import json
import logging
from typing import List, Dict
from .video_downloader import VideoDownloader
from ..models.task import SubtitleTask
from ..utils.process import ProcessRunner

logger = logging.getLogger(__name__)

class SubtitleProcessor:
    """Handles subtitle extraction and processing with nice priority"""
    
    def __init__(self, download_dir: str, video_downloader: VideoDownloader, nice_level: int = 19):
        """Initialize the subtitle processor with given configuration"""
        self.download_dir = download_dir
        self.video_downloader = video_downloader
        self.process_runner = ProcessRunner(nice_level)
    
    def _extension_for_codec(self, codec: str) -> str:
        """Get file extension for subtitle codec"""
        codec = codec.lower()
        if 'srt' in codec or 'ssa' in codec or 'ass' in codec:
            return codec.split('/')[-1].split('_')[0]
        if 'pgs' in codec or 'hdmv' in codec:
            return 'sup'
        if 'vobsub' in codec:
            return 'idx'
        return 'srt'
        
    async def extract_subtitles(self, video_path: str, task: SubtitleTask) -> List[Dict]:
        """Extract subtitles from video file using nice priority"""
        if sys.platform == "win32":
            cmd = ['mkvmerge', '-J', video_path]
        else:
            cmd = ['nice', f'-n{self.process_runner.nice_level}', 'mkvmerge', '-J', video_path]
        result = await self.process_runner.run_command(cmd)
        
        try:
            info = json.loads(result)
        except Exception as e:
            raise RuntimeError(f"Failed to parse mkvmerge JSON: {e}")
            
        tracks = info.get('tracks', [])
        if not tracks: 
            return []

        subtitle_tracks = []
        for t in tracks:
            if t.get('type') == 'subtitles':
                props = t.get('properties', {})
                lang = props.get('language') or props.get('languageIETF') or 'und'
                codec = t.get('codec') or props.get('codec_id') or props.get('codec') or ''
                track_id = t.get('id')
                ext = self._extension_for_codec(codec)
                subtitle_tracks.append({
                    'language': lang,
                    'format': ext,
                    'track_id': track_id
                })
                
        if not subtitle_tracks: 
            return []

        video_name = task.url if task.url else task.file_name
        video_name, _ = os.path.splitext(os.path.basename(video_name))
        extracted = []
        
        for t in subtitle_tracks:
            out_name = f"{video_name}_{t['language']}_{t['track_id']}.{t['format']}"
            out_path = os.path.join(self.download_dir, out_name)
            
            try:
                if sys.platform == "win32":
                    cmd = ['mkvextract', video_path, 'tracks', f"{t['track_id']}:{out_path}"]
                else:
                    cmd = ['nice', f'-n{self.process_runner.nice_level}', 'mkvextract', 
                        video_path, 'tracks', f"{t['track_id']}:{out_path}"]
                try:
                    await self.process_runner.run_command(cmd)
                except RuntimeError as e:
                    logger.warning(f"Failed to extract track {t['track_id']}: {e}")
                    continue
                
                if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    t['path'] = out_path
                    task.output_files.append(out_path)
                    extracted.append(t)
            except Exception as e:
                logger.warning(f"Failed to extract track {t['track_id']}: {e}")
                continue
                
        return extracted
        
    def cleanup(self) -> None:
        """Cleanup any temporary files or resources"""
        if os.path.exists(self.download_dir):
            for filename in os.listdir(self.download_dir):
                filepath = os.path.join(self.download_dir, filename)
                try:
                    if os.path.isfile(filepath):
                        os.remove(filepath)
                except Exception as e:
                    logger.error(f"Error cleaning up file {filepath}: {e}")
