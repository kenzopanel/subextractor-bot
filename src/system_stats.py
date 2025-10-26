import os
import time
import psutil
from typing import Dict

class SystemStats:
    def __init__(self):
        self.start_time = time.time()
        
    def get_stats(self) -> Dict[str, str]:
        """Get current system statistics formatted for display."""
        cpu = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        uptime = time.time() - self.start_time
        
        # Format uptime
        hours = int(uptime // 3600)
        minutes = int((uptime % 3600) // 60)
        seconds = int(uptime % 60)
        uptime_str = f"{hours}h{minutes}m{seconds}s"
        
        # Format disk space
        used_gb = disk.used / (1024**3)
        disk_str = f"{used_gb:.2f}GB [{disk.percent}%]"
        
        return {
            'cpu': f"{cpu:.1f}%",
            'ram': f"{memory.percent:.1f}%",
            'disk': disk_str,
            'uptime': uptime_str
        }