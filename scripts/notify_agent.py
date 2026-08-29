"""
NotificationAgent - 普通用户常驻通知代理
=========================================
以当前用户权限运行，监视文件队列，发送 WinRT Toast 通知（进通知中心）。
管理员权限的业务脚本通过写入队列文件触发通知。

队列目录: C:\ProgramData\MemoryManager\notify_queue\ （可通过 MM_NOTIFY_QUEUE 环境变量覆盖）
文件格式: .json → {title, message, is_error, ts}

依赖: pip install winrt-windows.ui.notifications winrt-windows.data.xml.dom
"""

import json
import os
import sys
import time
import glob

try:
    from winrt.windows.ui.notifications import ToastNotificationManager, ToastNotification
    from winrt.windows.data.xml.dom import XmlDocument
except ImportError:
    print("错误: 缺少 WinRT 依赖。请运行:")
    print("  pip install winrt-windows.ui.notifications winrt-windows.data.xml.dom")
    sys.exit(1)

QUEUE_DIR = os.environ.get("MM_NOTIFY_QUEUE", r"C:\ProgramData\MemoryManager\notify_queue")
POLL_INTERVAL = 2  # 秒
AUMID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"


def ensure_queue_dir():
    os.makedirs(QUEUE_DIR, exist_ok=True)


def send_toast(title: str, message: str, silent: bool = False):
    """发送 WinRT Toast 通知，进通知中心。"""
    audio_tag = '<audio silent="true"/>' if silent else ''
    xml = XmlDocument()
    xml.load_xml(
        '<toast scenario="reminder">'
        '<visual><binding template="ToastGeneric">'
        f"<text>{title}</text>"
        f"<text>{message}</text>"
        "</binding></visual>"
        f"{audio_tag}"
        "</toast>"
    )
    notification = ToastNotification(xml)
    notifier = ToastNotificationManager.create_toast_notifier_with_id(AUMID)
    notifier.show(notification)


def process_queue():
    """处理队列中的所有通知文件。"""
    pattern = os.path.join(QUEUE_DIR, "*.json")
    files = sorted(glob.glob(pattern))
    for f in files:
        # 先重命名为 .done 防止重复消费
        done_f = f + ".done"
        try:
            os.rename(f, done_f)
        except OSError:
            continue
        try:
            with open(done_f, "r", encoding="utf-8-sig") as fp:
                data = json.load(fp)
            title = data.get("title", "OpenClaw 记忆整理")
            message = data.get("message", "")
            silent = data.get("silent", False)
            send_toast(title, message, silent=silent)
        except Exception:
            pass
        finally:
            try:
                os.unlink(done_f)
            except OSError:
                pass


def main():
    ensure_queue_dir()
    pid_file = os.path.join(QUEUE_DIR, "agent.pid")
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))

    print(f"[NotificationAgent] 监视队列: {QUEUE_DIR}")
    print(f"[NotificationAgent] PID: {os.getpid()}")

    try:
        while True:
            process_queue()
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("\n[NotificationAgent] 已停止")
    finally:
        try:
            os.unlink(pid_file)
        except OSError:
            pass


if __name__ == "__main__":
    main()
