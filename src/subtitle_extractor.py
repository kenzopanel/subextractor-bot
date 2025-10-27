import os, tempfile, json, shutil, subprocess
from typing import List, Dict


class SubtitleExtractor:
    """Extract subtitle tracks from MKV using mkvmerge (identify) and mkvextract.

    This implementation uses subprocess to call `mkvmerge -J` to inspect the file
    and `mkvextract tracks` to extract subtitle tracks. The extractor returns a
    list of dicts: {"language": str, "format": str, "path": str}
    """

    def __init__(self, video_path: str):
        self.video_path = video_path
        self.temp_dir = tempfile.mkdtemp()

    def _run(self, args: List[str]) -> subprocess.CompletedProcess:
        return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def _extension_for_codec(self, codec: str) -> str:
        codec = (codec or "").lower()
        if 'subrip' in codec or 'srt' in codec:
            return 'srt'
        if 'ssa' in codec or 'ass' in codec:
            return 'ass'
        if 'pgs' in codec or 'hdmv' in codec or 'bd_sub' in codec or 'vobsub' in codec:
            return 'sup'
        
        for sep in ('/', '.'): 
            if sep in codec:
                token = codec.split(sep)[-1]
                if token:
                    return token.lower()
        return 'sub'

    def extract_subtitles(self) -> List[Dict[str, str]]:
        """Inspect MKV with mkvmerge -J and extract subtitle tracks via mkvextract.

        Returns:
            List[Dict[str,str]]: list of {language, format, path}
        """
        try:
            cp = self._run(['mkvmerge', '-J', self.video_path])
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"mkvmerge failed: {e.stderr.decode(errors='ignore')}")

        try:
            info = json.loads(cp.stdout)
        except Exception as e:
            raise RuntimeError(f"Failed to parse mkvmerge JSON: {e}")

        tracks = info.get('tracks', [])
        subtitle_tracks = []

        for t in tracks:
            if t.get('type') == 'subtitles':
                props = t.get('properties', {})
                lang = props.get('language') or props.get('languageIETF') or 'und'
                codec = t.get('codec') or props.get('codec_id') or props.get('codec') or ''
                track_id = t.get('id')
                ext = self._extension_for_codec(codec)
                subtitle_tracks.append({'language': lang, 'format': ext, 'track_id': track_id})

        if not subtitle_tracks:
            return []

        extracted = []
        for t in subtitle_tracks:
            out_name = f"{t['language']}_{t['track_id']}.{t['format']}"
            out_path = os.path.join(self.temp_dir, out_name)
            try:
                self._run(['mkvextract', 'tracks', self.video_path, f"{t['track_id']}:{out_path}"])
                if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    t['path'] = out_path
                    extracted.append(t)
            except subprocess.CalledProcessError as e:
                continue

        return extracted

    def cleanup(self):
        try:
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()