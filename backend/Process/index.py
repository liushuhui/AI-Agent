"""多进程教学示例（与 AI agent 主链路无关，独立演示 multiprocessing）。

演示三件事：Process 启动子进程、daemon 守护进程随主进程退出、
主进程边写 log.txt 边由守护进程统计行数。纯操作系统/并发练习，
不是助手业务代码，跑它不需要任何模型或数据库。
"""

import os
import time
from multiprocessing import Lock, Process


def speak():
    for index in range(10):
        print(f"我在说话{index}, 当前进程为：{os.getpid()}")
        time.sleep(1)


def study():
    for index in range(15):
        print(f"我在学习{index}, 当前进程为：{os.getpid()}")
        time.sleep(1)

def metor():
    while True:
        try:
            with open('log.txt', 'r', encoding='utf-8') as file:
                lines = sum(1 for _ in file)
        except FileNotFoundError:
            lines = 0
        print(f"文件总共{lines}行")
        time.sleep(1)

if __name__ == "__main__":
    print("我是主进程开始的日志")

    p1 = Process(target=metor, daemon=True)
    p1.start()

    with open('log.txt', 'a', encoding='utf-8') as file:
        for i in range(10):
            file.write(f'今天是{i}号\n')
            file.flush()
            time.sleep(1)
    print("我是主进程介素的日志")
