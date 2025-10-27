import os
import subprocess

def scale_worker():
    try:
        app_name = os.environ.get('HEROKU_APP_NAME')
        if app_name:
            subprocess.run(['heroku', 'ps:scale', 'worker=1', '-a', app_name])
            print("Successfully scaled worker dyno to 1")
    except Exception as e:
        print(f"Error scaling worker: {e}")

if __name__ == "__main__":
    scale_worker()