#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股15只赚钱天团股票监测面板
数据源：akshare（逐只获取，不拉取全市场数据）
功能：抓取15只股票 + 沪深300大盘数据，计算买入信号，输出 data.json
用法：pip install akshare pandas && python stock_monitor.py
更新频率：由 GitHub Actions 定时触发，每天 10:00 和 14:00（北京时间）各运行一次
"""

import akshare as ak
import pandas as pd
import json
import datetime
import time
import os
import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ============================================================
# 日志配置
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# ============================================================
# 关键：彻底禁用代理（解决沙箱/公司网络问题）
# akshare 内部使用 requests，必须同时清环境变量 + 设 session proxies={}
# ============================================================
# 1. 清除所有代理相关环境变量（大小写都清）
_proxy_keys = [k for k in os.environ.keys() if 'proxy' in k.lower()]
for k in _proxy_keys:
    del os.environ[k]
    logger.info(f"   🧹 已清除环境变量: {k}")

# 2. 设置 NO_PROXY（防止被系统代理拦截）
os.environ['NO_PROXY'] = '*'
os.environ['no_proxy'] = '*'

# 3. 配置 akshare 全局 Session（强制不走代理）
def init_akshare_session():
    session = requests.Session()
    # 关键：强制不走任何代理
    session.proxies = {'http': None, 'https': None}
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"]
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    session.timeout = 10
    ak._session = session
    logger.info("✅ akshare Session 已配置（超时10s，重试3次，禁用代理）")

init_akshare_session()


# ============================================================
# 通用工具
# ============================================================
def safe_float(val, default=None):
    if val is None:
        return default
    try:
        f = float(val)
        return f if pd.notna(f) else default
    except Exception:
        return default


def fetch_with_retry(func, *args, **kwargs):
    """
    带重试的数据获取（3次，间隔递增3s/6s/9s）
    """
    for i in range(3):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            wait = (i + 1) * 3
            logger.warning(f"   ⚠️  第{i+1}次失败: {e}，{wait}s后重试...")
            if i == 2:
                logger.error("   ❌ 所有重试失败")
                return None
            time.sleep(wait)


# ============================================================
# 获取沪深300指数（只读指数，不读全市场）
# ============================================================
def get_hs300_data():
    """
    获取沪深300指数行情 + 历史数据（近35日）
    用于计算成交量萎缩 & 恐慌信号
    """
    logger.info("📡 获取沪深300指数数据...")
    result = {
        "price": None,
        "change_pct": None,
        "volume": None,
        "volume_20d_avg": None,
        "volume_shrink": False,
        "volume_shrink_reason": "",
        "panic_signal": False,
        "panic_reason": "",
    }

    # 获取沪深300近期历史数据（含今日）
    today = datetime.date.today()
    start_date = (today - datetime.timedelta(days=35)).strftime("%Y%m%d")
    end_date = today.strftime("%Y%m%d")

    df_hist = fetch_with_retry(
        ak.stock_zh_index_daily_em,
        symbol="000300", start_date=start_date, end_date=end_date
    )
    if df_hist is None or df_hist.empty:
        logger.warning("   ⚠️  沪深300历史数据获取失败")
        return result

    # 兼容不同版本的列名
    date_col = df_hist.columns[0]
    vol_col = df_hist.columns[-2]
    chg_col = df_hist.columns[2] if len(df_hist.columns) > 2 else df_hist.columns[-1]
    for c in df_hist.columns:
        cl = c.strip().lower()
        if '日期' in cl or 'date' in cl:
            date_col = c
        if '成交' in cl or 'vol' in cl:
            vol_col = c
        if '涨跌' in cl or 'change' in cl or '涨跌幅' in cl:
            chg_col = c

    df_hist[date_col] = pd.to_datetime(df_hist[date_col]).dt.strftime("%Y%m%d")
    df_hist = df_hist.sort_values(date_col).reset_index(drop=True)
    df_hist['_vol_yi'] = df_hist[vol_col].astype(float) / 100000000

    latest_row = df_hist.iloc[-1]
    latest_volume_yi = float(latest_row['_vol_yi'])

    # 最新价 & 涨跌幅（ from 历史数据最后一行）
    close_val = safe_float(latest_row.get("收盘") or latest_row.get("close") or latest_row.get("指数收盘"))
    open_val = safe_float(latest_row.get("开盘") or latest_row.get("open") or latest_row.get("指数开盘"))
    result["price"] = close_val
    if close_val and open_val:
        result["change_pct"] = round((close_val - open_val) / open_val * 100, 2)

    logger.info(f"   ✅ 沪深300 最新价={result['price']}, 涨跌幅={result['change_pct']}%")

    # 计算成交量萎缩
    hist_20 = df_hist.iloc[-21:-1]
    if len(hist_20) >= 5:
        avg_20 = hist_20['_vol_yi'].astype(float).mean()
        result["volume_20d_avg"] = round(avg_20, 2)
        shrink1 = latest_volume_yi < avg_20 * 0.5
        shrink2 = latest_volume_yi < 2000
        result["volume_shrink"] = shrink1 and shrink2
        reasons = []
        if shrink1:
            reasons.append(f"成交额 {latest_volume_yi:.0f}亿 < 近20日均值({avg_20:.0f}亿)的50%")
        if shrink2:
            reasons.append(f"成交额 {latest_volume_yi:.0f}亿 < 2000亿")
        if reasons:
            result["volume_shrink_reason"] = "，且".join(reasons)
        else:
            result["volume_shrink_reason"] = f"成交额 {latest_volume_yi:.0f}亿，近20日均值 {avg_20:.0f}亿，未明显萎缩"
        logger.info(f"   ✅ 沪深300 成交量萎缩={result['volume_shrink']}")

    # 恐慌信号（单日跌幅 > 2.5%）
    if result["change_pct"] is not None:
        result["panic_signal"] = result["change_pct"] < -2.5
        if result["panic_signal"]:
            reason = f"沪深300单日跌幅 {result['change_pct']:.2f}%（超过2.5%警戒线）"
            if result["volume_shrink"]:
                reason += "，且成交量萎缩"
            result["panic_reason"] = reason
        else:
            result["panic_reason"] = f"沪深300涨跌幅 {result['change_pct']:.2f}%，未达恐慌线（-2.5%）"

    return result


# ============================================================
# 获取单只股票（只拉取指定股票，不碰全市场接口）
# ============================================================
def get_single_stock(code, name):
    """
    用 stock_zh_a_hist 获取单只股票最新数据
    这是最温和的接口，不会被限流
    返回 dict 或 None
    """
    def _fetch():
        today = datetime.date.today()
        start = (today - datetime.timedelta(days=5)).strftime("%Y%m%d")
        end = today.strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(
            symbol=code, period="daily",
            start_date=start, end_date=end, adjust="qfq"
        )
        if df is None or df.empty:
            return None
        latest = df.iloc[-1]
        close_val = safe_float(latest.get("收盘") or latest.get("close"))
        open_val = safe_float(latest.get("开盘") or latest.get("open"))
        chg = round((close_val - open_val) / open_val * 100, 2) if close_val and open_val else None
        return {
            "price": close_val,
            "change_pct": chg,
        }

    result = fetch_with_retry(_fetch)
    if result:
        logger.info(f"   ✅ {code} {name}: 现价={result['price']}, 涨跌幅={result['change_pct']}%")
    else:
        logger.warning(f"   ⚠️  {code} {name}: 数据获取失败")
    return result


# ============================================================
# 计算买入信号
# ============================================================
def calculate_buy_signals(stock_data, hs300_data):
    signals = []
    score = 0

    # 1. 恐慌性下跌
    if hs300_data.get("panic_signal"):
        signals.append("😱 恐慌性下跌：沪深300单日跌幅超2.5%")
        score += 40
    elif hs300_data.get("change_pct") is not None and hs300_data["change_pct"] < -1.5:
        signals.append("⚠️ 大盘弱势：沪深300今日下跌")
        score += 10

    # 2. 成交量萎缩
    if hs300_data.get("volume_shrink"):
        signals.append("📉 成交量萎缩：市场观望，可能临近底部")
        score += 20

    # 3. 年底布局窗口
    month = datetime.date.today().month
    if month in [11, 12]:
        signals.append("📅 年底布局窗口：机构调仓，历史上有年底行情")
        score += 15

    # 4. 分红季
    if month in [5, 6, 7]:
        signals.append("🎁 临近分红季（6-7月），现在布局可吃分红")
        score += 15

    # 5. 估值安全边际
    target = stock_data.get("dividend_yield_target", 0)
    real = stock_data.get("dividend_yield_real") or 0
    if target > 0 and real >= target * 0.7:
        signals.append(f"💰 股息率 {real:.2f}% 接近目标 {target:.2f}%（已达70%以上）")
        score += 20

    if not signals:
        signals.append("😴 当前无明显买入信号，建议观望")

    if score >= 50:
        signal = "green"
        advice = "🟢 可以买入（多个买入窗口叠加）"
    elif score >= 25:
        signal = "yellow"
        advice = "🟡 可以小幅建仓（部分信号出现）"
    else:
        signal = "red"
        advice = "🔴 不建议（等待更好买点）"

    return {
        "signal": signal,
        "score": score,
        "reasons": signals,
        "advice": advice,
    }


# ============================================================
# 股票列表
# ============================================================
STOCK_LIST = [
    {"code": "601328", "name": "交通银行", "category": "银行", "dividend_yield_target": 5.62},
    {"code": "601166", "name": "兴业银行", "category": "银行", "dividend_yield_target": 5.21},
    {"code": "601998", "name": "中信银行", "category": "银行", "dividend_yield_target": 5.17},
    {"code": "601288", "name": "农业银行", "category": "银行", "dividend_yield_target": 4.61},
    {"code": "601398", "name": "工商银行", "category": "银行", "dividend_yield_target": 4.22},
    {"code": "601939", "name": "建设银行", "category": "银行", "dividend_yield_target": 4.05},
    {"code": "601988", "name": "中国银行", "category": "银行", "dividend_yield_target": 3.97},
    {"code": "601658", "name": "邮储银行", "category": "银行", "dividend_yield_target": 3.43},
    {"code": "600036", "name": "招商银行", "category": "银行", "dividend_yield_target": 2.85},
    {"code": "600941", "name": "中国移动", "category": "通信", "dividend_yield_target": 7.85},
    {"code": "600938", "name": "中国海油", "category": "能源", "dividend_yield_target": 6.28},
    {"code": "601857", "name": "中国石油", "category": "能源", "dividend_yield_target": 4.83},
    {"code": "601318", "name": "中国平安", "category": "保险", "dividend_yield_target": 3.86},
    {"code": "601628", "name": "中国人寿", "category": "保险", "dividend_yield_target": 3.02},
    {"code": "300750", "name": "宁德时代", "category": "成长股", "dividend_yield_target": 0.41},
]


# ============================================================
# 主流程
# ============================================================
def main():
    logger.info("=" * 50)
    logger.info("🚀 A股15只赚钱天团股票监测系统 开始运行")
    logger.info("=" * 50)

    # 1. 获取沪深300指数（只读1个指数，不读全市场）
    hs300_data = get_hs300_data()
    logger.info(f"   沪深300: {hs300_data['price']} ({hs300_data['change_pct']}%)")

    # 2. 逐只获取15只股票（每只间隔2秒，避免限流）
    categories = {"银行": [], "能源通信": [], "保险": [], "成长股": []}
    summary = {"total_stocks": 15, "green_count": 0, "yellow_count": 0, "red_count": 0}

    for idx, stock in enumerate(STOCK_LIST):
        code = stock["code"]
        name = stock["name"]
        category = stock["category"]
        logger.info(f"   🔍 [{idx+1}/15] {code} {name} ...")

        # 只拉取这一只股票的数据（不碰全市场接口）
        info = get_single_stock(code, name)

        if info is None:
            info = {"price": None, "change_pct": None}

        # 组装股票数据
        stock_data = {
            "code": code,
            "name": name,
            "category": category,
            "price": info.get("price"),
            "change_pct": info.get("change_pct"),
            "pe": None,
            "pb": None,
            "pev": None,
            "dividend_yield_real": None,
            "dividend_yield_target": stock["dividend_yield_target"],
            "market_cap": None,
        }

        # 计算买入信号
        signal_result = calculate_buy_signals(stock_data, hs300_data)
        stock_data.update(signal_result)

        # 分类归档
        if category == "银行":
            categories["银行"].append(stock_data)
        elif category in ("能源", "通信"):
            categories["能源通信"].append(stock_data)
        elif category == "保险":
            categories["保险"].append(stock_data)
        elif category == "成长股":
            categories["成长股"].append(stock_data)

        # 统计
        sig = signal_result["signal"]
        if sig == "green":
            summary["green_count"] += 1
        elif sig == "yellow":
            summary["yellow_count"] += 1
        else:
            summary["red_count"] += 1

        # 每只间隔2秒，避免触发限流
        if idx < len(STOCK_LIST) - 1:
            time.sleep(2)

    # 3. 输出 data.json
    output = {
        "update_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_date": datetime.date.today().strftime("%Y-%m-%d"),
        "hs300": hs300_data,
        "categories": categories,
        "summary": summary,
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    logger.info("=" * 50)
    logger.info(f"✅ 数据已保存到 data.json")
    logger.info(f"   沪深300: {hs300_data['price']} ({hs300_data['change_pct']}%)")
    logger.info(f"   信号统计: 🟢{summary['green_count']} 🟡{summary['yellow_count']} 🔴{summary['red_count']}")
    logger.info("=" * 50)


if __name__ == "__main__":
    main()
