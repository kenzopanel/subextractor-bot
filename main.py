import os
from src.bot import SubtitleBot

if __name__ == "__main__":
    os.environ['APP_DIR'] = os.path.dirname(os.path.abspath(__file__))
    bot = SubtitleBot()
    bot.run()