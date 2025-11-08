import os, psutil, logging
from datetime import datetime

logger = logging.getLogger(__name__)

class SystemStats:
    """Collects and formats system statistics."""
    
    def __init__(self):
        self.start_time = datetime.now()
        
    def get_stats(self) -> dict:
        """Get current system statistics"""
        try:
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory().percent
            
            # Get disk usage for download directory
            disk = psutil.disk_usage(os.path.dirname(os.path.abspath(__file__)))
            disk_free = disk.free / (1024 * 1024 * 1024)  # Convert to GB
            disk_percent = disk.percent
            
            # Calculate uptime
            uptime = datetime.now() - self.start_time
            hours = int(uptime.total_seconds() // 3600)
            minutes = int((uptime.total_seconds() % 3600) // 60)
            seconds = int(uptime.total_seconds() % 60)
            
            uptime_str = f"{hours}h{minutes:02d}m{seconds:02d}s"
            
            return {
                'cpu': f"{cpu:.1f}%",
                'ram': f"{ram:.1f}%",
                'disk': f"{disk_free:.2f}GB [{disk_percent}%]",
                'uptime': uptime_str
            }
            
        except Exception as e:
            logger.error(f"Error getting system stats: {e}")
            return {
                'cpu': "N/A",
                'ram': "N/A",
                'disk': "N/A",
                'uptime': "N/A"
            }