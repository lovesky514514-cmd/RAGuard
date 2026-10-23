"""等待本地 Go 沙箱实际就绪，不使用固定启动延时。"""

import time
import urllib.error
import urllib.request


def main():
    for _ in range(300):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8181/healthz", timeout=0.2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(0.1)
    raise SystemExit("Go 沙箱未就绪，请检查 8181 端口和 Go 编译日志")


if __name__ == "__main__":
    main()
