import os
from watchgod import run_process

def run_server():
    os.system("py TradingServer.py")  # 서버 실행 명령

if __name__ == "__main__":
    run_process('.', run_server)  # 현재 폴더 안 .py 파일 변경 감시
