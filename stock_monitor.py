#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股15只赚钱天团股票监测面板
数据源：akshare（带超时+重试，优先em，降级sina）
功能：抓取15只股票实时数据 + 沪深300大盘数据，计算买入信号，输出 data.json
用法：pip install akshare pandas && python stock_monitor.py
更新频率：由 GitHub Actions 定时触发，每天 10:00 和 14:00（北京时间）各运行一次
"""

import akshare as ak
import pandas as pd
import json
import datetime
import time
import os
import sys
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
# 配置 akshare 的 requests Session（关键！解决境外服务器超时问题）
# ============================================================
def init_akshare_session():
    """配置 akshare 的全局 Session：超时10秒 + 自动重试3次"""
    session = requests.Session()
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
    logger.info("✅ akshare Session 已配置（超时10s，重试3次）")

init_akshare_session()

# 绕过代理（避免公司网络/代理导致 akshare 请求失败）
os.environ['NO_PROXY'] = '*'
os.environ.pop('HTTP_PROXY', None)
os.environ.pop('HTTPS_PROXY', None)
os.environ.pop('http_proxy', None)
os.environ.pop('https_proxy', None)


# ============================================================
# 通用工具
# ============================================================
def safe_float(val, default=None):
    """安全转换为 float"""
    if val is None:
        return default
    try:
        f = float(val)
        return f if pd.notna(f) else default
    except Exception:
        return default


def fetch_data_with_retry(func, *args, **kwargs):
    """
    通用数据获取函数，带重试（3次，间隔递增）
    用法：df = fetch_data_with_retry(ak.stock_zh_a_spot_em)
    """
    max_retries = 3
    for i in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            wait = (i + 1) * 3  # 3s, 6s, 9s 递增间隔
            logger.warning(f"   ⚠️  第{i+1}次调用失败: {e}，{wait}s后重试...")
            if i == max_retries - 1:
                logger.error("   ❌ 所有重试失败")
                return None
            time.sleep(wait)


# ============================================================
# 获取沪深300指数数据
# ============================================================
def get_hs300_by_hist():
    """
    备用方案：用 stock_zh_index_daily_em 获取沪深300最新数据
    """
    try:
        today = datetime.date.today()
        start_date = (today - datetime.timedelta(days=5)).strftime("%Y%m%d")
        end_date = today.strftime("%Y%m%d")
        df = fetch_data_with_retry(
            ak.stock_zh_index_daily_em,
            symbol="000300", start_date=start_date, end_date=end_date
        )
        if df is None or df.empty:
            return None
        latest = df.iloc[-1]
        close_val = safe_float(latest.get("收盘") or latest.get("close") or latest.get("指数收盘"))
        open_val = safe_float(latest.get("开盘") or latest.get("open") or latest.get("指数开盘"))
        vol_raw = safe_float(latest.get("成交额") or latest.get("volume"))
        chg = round((close_val - open_val) / open_val * 100, 2) if close_val and open_val else None
        result = {
            "price": close_val,
            "change_pct": chg,
            "volume": round(vol_raw / 100000000, 2) if vol_raw else None,
        }
        logger.info(f"   ✅ [备用] 沪深300 最新价={result['price']}, 涨跌幅={result['change_pct']}%")
        return result
    except Exception as e:
        logger.warning(f"   ⚠️  沪深300 历史备用方案失败: {e}")
        return None


def get_hs300_data():
    """
    获取沪深300指数实时行情 + 历史数据
    优先用 stock_zh_index_spot_em，失败则用历史数据备用
    """
    logger.info("📡 正在获取沪深300指数数据...")
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

    # —— 步骤1：尝试获取实时行情（带重试）——
    df_index = fetch_data_with_retry(ak.stock_zh_index_spot_em)
    if df_index is not None and not df_index.empty:
        hs300_row = df_index[df_index["代码"] == "000300"]
        if not hs300_row.empty:
            row = hs300_row.iloc[0]
            result["change_pct"] = safe_float(row.get("涨跌幅"))
            result["price"] = safe_float(row.get("最新价"))
            vol_raw = safe_float(row.get("成交额"))
            result["volume"] = round(vol_raw / 100000000, 2) if vol_raw else None
            logger.info(f"   ✅ 沪深300 最新价={result['price']}, 涨跌幅={result['change_pct']}%, 成交额={result['volume']}亿")

    # —— 步骤2：实时行情失败，用历史数据补最新价 ——
    if result["price"] is None:
        hist_rt = get_hs300_by_hist()
        if hist_rt:
            result["price"] = hist_rt.get("price")
            result["change_pct"] = hist_rt.get("change_pct")
            if result["volume"] is None:
                result["volume"] = hist_rt.get("volume")

    # —— 步骤3：获取近30日历史数据（计算成交量均线和恐慌信号）——
    try:
        today = datetime.date.today()
        start_date = (today - datetime.timedelta(days=35)).strftime("%Y%m%d")
        end_date = today.strftime("%Y%m%d")
        df_hist = fetch_data_with_retry(
            ak.stock_zh_index_daily_em,
            symbol="000300", start_date=start_date, end_date=end_date
        )
        if df_hist is not None and not df_hist.empty:
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
                if '涨跌' in cl or 'change' in cl:
                    chg_col = c

            df_hist[date_col] = pd.to_datetime(df_hist[date_col]).dt.strftime("%Y%m%d")
            df_hist = df_hist.sort_values(date_col).reset_index(drop=True)
            df_hist['_vol_yi'] = df_hist[vol_col].astype(float) / 100000000

            latest_row = df_hist.iloc[-1]
            latest_volume_yi = float(latest_row['_vol_yi'])

            hist_20 = df_hist.iloc[-21:-1]
            if len(hist_20) >= 5:
                avg_20 = hist_20['_vol_yi'].astype(float).mean()
                result["volume_20d_avg"] = round(avg_20, 2)
                shrink1 = latest_volume_yi < avg_20 * 0.5
                shrink2 = latest_volume_yi < 2000
                result["volume_shrink"] = shrink1 and shrink2
                if shrink1:
                    result["volume_shrink_reason"] = f"成交额 {latest_volume_yi:.0f}亿 < 近20日均值({avg_20:.0f}亿)的50%"
                if shrink2:
                    if result["volume_shrink_reason"]:
                        result["volume_shrink_reason"] += "，且"
                    result["volume_shrink_reason"] += f"成交额 {latest_volume_yi:.0f}亿 < 2000亿"
                if not result["volume_shrink"]:
                    result["volume_shrink_reason"] = f"成交额 {latest_volume_yi:.0f}亿，近20日均值 {avg_20:.0f}亿，未明显萎缩"
                logger.info(f"   ✅ 沪深300 成交量萎缩={result['volume_shrink']}")

            # 恐慌信号：单日跌幅 > 2.5%
            if result["change_pct"] is not None:
                change = result["change_pct"]
                result["panic_signal"] = change < -2.5
                if result["panic_signal"]:
                    reason = f"沪深300单日跌幅 {change:.2f}%（超过2.5%警戒线）"
                    if result["volume_shrink"]:
                        reason += "，且成交量萎缩"
                    result["panic_reason"] = reason
                else:
                    result["panic_reason"] = f"沪深300涨跌幅 {change:.2f}%，未达恐慌线（-2.5%）"
    except Exception as e:
        logger.warning(f"   ⚠️  获取沪深300历史数据失败（不影响主功能）: {e}")

    return result


# ============================================================
# 获取单只股票数据（带备用方案）
# ============================================================
def get_stock_by_hist(code, name):
    """
    备用方案：用 stock_zh_a_hist 获取单只股票的最新数据
    带重试，避免在境外环境失败
    """
    def _fetch():
        today = datetime.date.today()
        start = (today - datetime.timedelta(days=5)).strftime("%Y%m%d")
        end = today.strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, end_date=end, adjust="qfq")
        if df is None or df.empty:
            return None
        latest = df.iloc[-1]
        close_val = safe_float(latest.get("收盘") or latest.get("close"))
        open_val = safe_float(latest.get("开盘") or latest.get("open"))
        chg = round((close_val - open_val) / open_val * 100, 2) if close_val and open_val else None
        return {
            "price": close_val,
            "change_pct": chg,
            "pe": None,
            "pb": None,
            "dividend_yield": None,
            "market_cap": None,
        }

    result = fetch_data_with_retry(_fetch)
    if result is None:
        logger.warning(f"   ⚠️  {code} {name} 备用方案失败")
    return result


def get_realtime_data():
    """
    获取全部A股实时行情（东方财富）
    在 GitHub Actions 环境中直接使用备用方案（更可靠）
    """
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        logger.info("   📡 检测到 GitHub Actions 环境，直接使用逐只股票备用方案")
        return None

    logger.info("📡 方案1：正在获取A股实时行情（东方财富）...")
    df = fetch_data_with_retry(ak.stock_zh_a_spot_em)
    if df is not None and not df.empty:
        logger.info(f"   ✅ 获取到 {len(df)} 只股票实时数据")
        return df
    logger.info("   ❌ 方案1失败，将使用逐只股票备用方案")
    return None


# ============================================================
# 计算买入信号
# ============================================================
def calculate_buy_signals(stock_data, hs300_data):
    """计算4大买入窗口信号"""
    signals = []
    score = 0

    # 1. 恐慌性下跌（大盘信号）
    if hs300_data.get("panic_signal"):
        signals.append("😱 恐慌性下跌：沪深300单日跌幅超2.5%")
        score += 40
    elif hs300_data.get("change_pct") is not None and hs300_data["change_pct"] < -1.5:
        signals.append("⚠️ 大盘弱势：沪深300今日下跌")
        score += 10

    # 2. 成交量萎缩（大盘信号）
    if hs300_data.get("volume_shrink"):
        signals.append("📊 成交量萎缩：市场观望，可能临近底部")
        score += 20

    # 3. 年底布局窗口（11-12月）
    month = datetime.date.today().month
    if month in [11, 12]:
        signals.append("📅 年底布局窗口：机构调仓，历史上有年底行情")
        score += 15

    # 4. 分红季（5-7月）
    if month in [5, 6, 7]:
        signals.append("🎁 临近分红季（6-7月），现在布局可吃分红")
        score += 15

    # 5. 估值安全边际（个股息率 > 目标70%）
    target = stock_data.get("dividend_yield_target", 0)
    real = stock_data.get("dividend_yield_real") or 0
    if target > 0 and real >= target * 0.7:
        signals.append(f"💰 股息率 {real:.2f}% 接近目标 {target:.2f}%（已达70%以上）")
        score += 20

    # 6. 技术面：PE < 目标PE的80%（如果有PE数据）
    pe = stock_data.get("pe")
    if pe is not None:
        # 简单的PE估值判断（不同类型股票有不同标准，这里用通用标准）
        if stock_data["category"] == "银行" and pe < 6:
            signals.append(f"📊 银行股PE={pe:.1f} < 6，估值偏低")
            score += 10
        elif stock_data["category"] == "成长股" and pe < 15:
            signals.append(f"🚀 成长股PE={pe:.1ff} < 15，波段买入机会")
            score += 15

    # 如果没有任何信号
    if not signals:
        signals.append("😴 当前无明显买入信号，建议观望")

    # 确定信号等级
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
# 构建股票数据
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


def build_stock_data():
    """构建所有股票的数据（逐只获取，带延迟避免限流）"""
    logger.info("📊 开始获取15只股票数据...")
    categories = {"银行": [], "能源通信": [], "保险": [], "成长股": []}
    summary = {"total_stocks": 15, "green_count": 0, "yellow_count": 0, "red_count": 0}

    for idx, stock in enumerate(STOCK_LIST):
        code = stock["code"]
        name = stock["name"]
        category = stock["category"]
        logger.info(f"   🔍 处理 {code} {name} ({idx+1}/15)...")

        # 始终使用逐只获取（避免批量请求触发限流）
        info = get_stock_by_hist(code, name)

        if info is None:
            info = {
                "price": None, "change_pct": None, "pe": None, "pb": None,
                "dividend_yield": None, "market_cap": None,
                "error": "数据获取失败"
            }

        # 组装最终数据
        stock_data = {
            "code": code,
            "name": name,
            "category": category,
            "price": info.get("price"),
            "change_pct": info.get("change_pct"),
            "pe": info.get("pe"),
            "pb": info.get("pb"),
            "pev": info.get("pev"),
            "dividend_yield_real": info.get("dividend_yield"),
            "dividend_yield_target": stock["dividend_yield_target"],
            "market_cap": info.get("market_cap"),
        }

        # 计算买入信号（先不包含 hs300，后面统一补）
        signal_result = calculate_buy_signals(stock_data, {})
        stock_data.update(signal_result)

        # 分类归档
        if category == "银行":
            categories["银行"].append(stock_data)
        elif category in ["能源", "通信"]:
            categories["能源通信"].append(stock_data)
        elif category == "保险":
            categories["保险"].append(stock_data)
        elif category == "成长股":
            categories["成长股"].append(stock_data)

        # 统计信号
        sig = signal_result["signal"]
        if sig == "green":
            summary["green_count"] += 1
        elif sig == "yellow":
            summary["yellow_count"] += 1
        else:
            summary["red_count"] += 1

        # 每只股票请求后延迟 2 秒，避免触发限流
        if idx < len(STOCK_LIST) - 1:
            time.sleep(2)

    return categories, summary


# ============================================================
# 主函数
# ============================================================
def main():
    logger.info("=" * 50)
    logger.info("🚀 A股15只赚钱天团股票监测系统 开始运行")
    logger.info("=" * 50)

    # 获取沪深300数据
    hs300_data = get_hs300_data()
    logger.info(f"沪深300 结果: price={hs300_data['price']}, change_pct={hs300_data['change_pct']}%")

    # 获取所有股票数据（不含 hs300 信号）
    categories, _ = build_stock_data()

    # 用真实的沪深300数据重新计算信号
    for cat_stocks in categories.values():
        for stock in cat_stocks:
            signal_result = calculate_buy_signals(stock, hs300_data)
            stock.update(signal_result)

    # 重新统计信号
    summary = {"total_stocks": 15, "green_count": 0, "yellow_count": 0, "red_count": 0}
    for cat_stocks in categories.values():
        for stock in cat_stocks:
            sig = stock["signal"]
            if sig == "green":
                summary["green_count"] += 1
            elif sig == "yellow":
                summary["yellow_count"] += 1
            else:
                summary["red_count"] += 1

    # 输出 data.json
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
