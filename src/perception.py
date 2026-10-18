"""时空感知（借鉴 Gemini 方案的 get_environment_context）。

捕获当前时间、时段、天气、粗略位置，注入 Prompt 实现「贴心」。

天气改为后台线程 + 缓存：wttr.in 在国内访问慢（可达 16s），若每次对话同步
请求会严重卡顿，故由 `start_weather_updater()` 后台定期刷新，对话时直接读缓存。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Dict

import requests

# 天气缓存：后台线程写、对话线程读，用锁保护
_WEATHER_CACHE: Dict = {"text": "", "ts": 0.0}
_WEATHER_LOCK = threading.Lock()
_WEATHER_TTL = 1800  # 30 分钟刷新一次


def _time_period(hour: int) -> str:
    if 5 <= hour < 8:
        return "清晨"
    if 8 <= hour < 11:
        return "上午"
    if 11 <= hour < 13:
        return "中午"
    if 13 <= hour < 18:
        return "下午"
    if 18 <= hour < 23:
        return "晚上"
    return "深夜"


def _update_weather() -> None:
    """请求 wttr.in 更新天气缓存（后台线程调用，失败保留旧值）。"""
    global _WEATHER_CACHE
    try:
        resp = requests.get("https://wttr.in/?format=j1", timeout=10)
        data = resp.json()
        area = data["nearest_area"][0]["areaName"][0]["value"]
        cond = data["current_condition"][0]["weatherDesc"][0]["value"]
        temp = data["current_condition"][0]["temp_C"]
        text = f"地点:{area} 天气:{cond} 温度:{temp}℃"
        with _WEATHER_LOCK:
            _WEATHER_CACHE = {"text": text, "ts": time.time()}
    except Exception:
        pass  # 网络异常时保留旧缓存


def start_weather_updater(interval: int = _WEATHER_TTL) -> None:
    """后台线程定期更新天气，不阻塞对话。"""
    def _loop() -> None:
        _update_weather()  # 启动先拉一次
        while True:
            time.sleep(interval)
            _update_weather()

    t = threading.Thread(target=_loop, daemon=True, name="weather-updater")
    t.start()


def _fetch_weather() -> str:
    """读天气缓存（0 阻塞）。"""
    with _WEATHER_LOCK:
        return _WEATHER_CACHE["text"]


def get_environment_context() -> Dict:
    """返回时间/时段/天气，供 context 组装器注入。天气走缓存，不阻塞。"""
    now = datetime.now()
    hour = now.hour
    return {
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "time_period": _time_period(hour),
        "hour": hour,
        "weather": _fetch_weather(),
    }
