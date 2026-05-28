# -*- coding: utf-8 -*-
"""
A股15只赚钱天团股票监测系统
使用 yfinance 获取数据（境外服务器稳定）
分红数据从 forward_dividend.json 读取
"""

import json
import datetime
import time
import logging
import yfinance as yf
import pandas as pd
import numpy as np

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
# 读取分红数据（从 JSON 文件）
# ============================================================
FORWARD_DIVIDEND_FILE = "forward_dividend.json"

def load_forward_dividend():
    """读取前瞻分红数据"""
    try:
        with open(FORWARD_DIVIDEND_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info(f"✅ 已读取分红数据: {FORWARD_DIVIDEND_FILE}")
        return data
    except Exception as e:
        logger.warning(f"⚠️  读取分红数据失败: {e}，将使用空数据")
        return {}

# 加载分红数据
FORWARD_DIVIDEND_DATA = load_forward_dividend()

# ============================================================
# 股票列表（yfinance 格式代码）
# ============================================================
STOCK_MAP = {
    "601328": {"name": "交通银行", "category": "银行", "ticker": "601328.SS"},
    "601166": {"name": "兴业银行", "category": "银行", "ticker": "601166.SS"},
    "601998": {"name": "中信银行", "category": "银行", "ticker": "601998.SS"},
    "601288": {"name": "农业银行", "category": "银行", "ticker": "601288.SS"},
    "601398": {"name": "工商银行", "category": "银行", "ticker": "601398.SS"},
    "601939": {"name": "建设银行", "category": "银行", "ticker": "601939.SS"},
    "601988": {"name": "中国银行", "category": "银行", "ticker": "601988.SS"},
    "601658": {"name": "邮储银行", "category": "银行", "ticker": "601658.SS"},
    "600036": {"name": "招商银行", "category": "银行", "ticker": "600036.SS"},
    "600941": {"name": "中国移动", "category": "能源通信", "ticker": "600941.SS"},
    "600938": {"name": "中国海油", "category": "能源通信", "ticker": "600938.SS"},
    "601857": {"name": "中国石油", "category": "能源通信", "ticker": "601857.SS"},
    "601318": {"name": "中国平安", "category": "保险", "ticker": "601318.SS"},
    "601628": {"name": "中国人寿", "category": "保险", "ticker": "601628.SS"},
    "300750": {"name": "宁德时代", "category": "成长股", "ticker": "300750.SZ"},
}

# 目标股息率（用于信号判断）
DIVIDEND_TARGET = {
    "601328": 5.62, "601166": 5.21, "601998": 5.17, "601288": 4.61,
    "601398": 4.22, "601939": 4.05, "601988": 3.97, "601658": 3.43,
    "600036": 2.85, "600941": 7.85, "600938": 6.28, "601857": 4.83,
    "601318": 3.86, "601628": 3.02, "300750": 0.41,
}

# ============================================================
# 工具函数
# ============================================================
def safe_float(val, default=None):
    """安全转换为 float（处理 numpy/pandas 类型）"""
    try:
        if val is None:
            return default
        # 处理 numpy/pandas 类型
        if isinstance(val, (np.integer, np.floating)):
            val = float(val)
        if isinstance(val, (np.bool_, bool)):
            return default
        return float(val)
    except (ValueError, TypeError, OverflowError):
        return default


def safe_bool(val, default=False):
    """安全转换为 bool（处理 numpy/pandas 类型）"""
    try:
        if val is None:
            return default
        # 处理 numpy bool
        if isinstance(val, np.bool_):
            return bool(val)
        return bool(val)
    except (ValueError, TypeError):
        return default


def convert_to_json_serializable(obj):
    """递归转换对象为 JSON 可序列化类型"""
    if isinstance(obj, dict):
        return {k: convert_to_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [convert_to_json_serializable(v) for v in obj]
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.bool_):
        return bool(obj)
    elif isinstance(obj, pd.Timestamp):
        return obj.strftime('%Y-%m-%d')
    elif hasattr(obj, 'isoformat'):  # datetime
        return obj.isoformat()
    else:
        return obj


def get_stock_data(ticker, name, forward_dividend=None):
    """
    获取单只股票数据（使用 yfinance）
    返回：dict with price, change_pct, pe, pb, forward_dividend_yield, market_cap
    """
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        
        # 获取当前价格和涨跌幅
        price = safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
        previous_close = safe_float(info.get("previousClose"))
        
        if price and previous_close:
            change_pct = round((price - previous_close) / previous_close * 100, 2)
        else:
            change_pct = None
        
        # 获取估值指标
        pe = safe_float(info.get("trailingPE") or info.get("forwardPE"))
        pb = safe_float(info.get("priceToBook"))
        market_cap = safe_float(info.get("marketCap"))
        
        # 计算前瞻股息率（基于预计分红）
        forward_dividend_yield = None
        if forward_dividend and price:
            forward_dividend_yield = round((forward_dividend / price) * 100, 2)
        
        logger.info(f"   ✅ {name} ({ticker}): 价格={price}, 涨跌幅={change_pct}%, PE={pe}, PB={pb}, 前瞻股息率={forward_dividend_yield}%")
        
        return {
            "price": price,
            "change_pct": change_pct,
            "pe": pe,
            "pb": pb,
            "forward_dividend_yield": forward_dividend_yield,
            "market_cap": round(market_cap / 100000000, 2) if market_cap else None,  # 转换为亿
        }
    except Exception as e:
        logger.warning(f"   ⚠️  {name} ({ticker}) 获取失败: {e}")
        return None


def get_hs300_data():
    """
    获取沪深300指数数据（使用 yfinance）
    返回：dict with price, change_pct, volume, volume_20d_avg, volume_shrink, panic_signal
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
    
    try:
        # 沪深300 ETF (510300.SS) 作为替代
        hs300 = yf.Ticker("000300.SS")
        info = hs300.info
        
        price = safe_float(info.get("regularMarketPrice"))
        previous_close = safe_float(info.get("previousClose"))
        
        if price and previous_close:
            change_pct = round((price - previous_close) / previous_close * 100, 2)
            result["price"] = price
            result["change_pct"] = change_pct
            logger.info(f"   ✅ 沪深300: 价格={price}, 涨跌幅={change_pct}%")
        
        # 获取历史数据（计算成交量和恐慌信号）
        hist = hs300.history(period="1mo")
        if not hist.empty:
            # 计算20日平均成交量
            hist['volume_ma20'] = hist['Volume'].rolling(window=20).mean()
            latest = hist.iloc[-1]
            latest_volume = latest['Volume'] / 100000000  # 转换为亿
            avg_20 = latest['volume_ma20'] / 100000000
            
            result["volume"] = round(latest_volume, 2)
            result["volume_20d_avg"] = round(avg_20, 2)
            
            # 成交量萎缩判断
            shrink1 = latest_volume < avg_20 * 0.5
            shrink2 = latest_volume < 2000
            result["volume_shrink"] = shrink1 and shrink2
            
            if shrink1:
                result["volume_shrink_reason"] = f"成交额 {latest_volume:.0f}亿 < 近20日均值({avg_20:.0f}亿)的50%"
            if shrink2:
                if result["volume_shrink_reason"]:
                    result["volume_shrink_reason"] += "，且"
                result["volume_shrink_reason"] += f"成交额 {latest_volume:.0f}亿 < 2000亿"
            if not result["volume_shrink"]:
                result["volume_shrink_reason"] = f"成交额 {latest_volume:.0f}亿，近20日均值 {avg_20:.0f}亿，未明显萎缩"
            
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
        logger.warning(f"   ⚠️  沪深300数据获取失败（不影响主功能）: {e}")
    
    return result


def calculate_buy_signals(stock_data, hs300_data):
    """
    计算买入信号
    根据用户的选股标准：
    - 银行：PE<5, PB<0.65, 股息率>5%
    - 能源通信：股息率>6%
    - 保险：PEV<0.7（用PB近似）
    - 成长股：PE<15
    """
    category = stock_data["category"]
    pe = stock_data.get("pe")
    pb = stock_data.get("pb")
    dividend = stock_data.get("dividend_yield")
    target = stock_data.get("dividend_yield_target", 0)
    
    reasons = []
    score = 0
    
    # 行业特定信号
    if category == "银行":
        if pe and pe < 5:
            reasons.append(f"✅ PE={pe:.2f} < 5（低估）")
            score += 30
        if pb and pb < 0.65:
            reasons.append(f"✅ PB={pb:.2f} < 0.65（破净）")
            score += 30
        if dividend and dividend > 5:
            reasons.append(f"✅ 股息率={dividend:.2f}% > 5%（高股息）")
            score += 20
        if dividend and target and dividend >= target * 0.8:
            reasons.append(f"🎁 股息率={dividend:.2f}%，接近目标({target}%)")
            score += 20
    
    elif category == "能源通信":
        if dividend and dividend > 6:
            reasons.append(f"✅ 股息率={dividend:.2f}% > 6%（高股息）")
            score += 40
        if pe and pe < 10:
            reasons.append(f"✅ PE={pe:.2f} < 10（低估）")
            score += 30
    
    elif category == "保险":
        if pb and pb < 0.7:
            reasons.append(f"✅ PB={pb:.2f} < 0.7（低估）")
            score += 40
        if dividend and dividend > 3:
            reasons.append(f"✅ 股息率={dividend:.2f}% > 3%")
            score += 30
    
    elif category == "成长股":
        if pe and pe < 15:
            reasons.append(f"🚀 PE={pe:.2f} < 15（波段机会）")
            score += 50
        if pb and pb < 3:
            reasons.append(f"✅ PB={pb:.2f} < 3（合理估值）")
            score += 30
    
    # 大盘恐慌信号（加分）
    if hs300_data.get("panic_signal"):
        reasons.append(f"🔥 大盘恐慌：{hs300_data['panic_reason']}")
        score += 20
    elif hs300_data.get("volume_shrink"):
        reasons.append(f"📉 成交量萎缩：{hs300_data['volume_shrink_reason']}")
        score += 10
    
    # 分红季（6-7月）
    now = datetime.datetime.now()
    if now.month in [6, 7]:
        reasons.append("🎁 临近分红季（6-7月），现在布局可吃分红")
        score += 15
    
    # 年末（12月）
    if now.month == 12:
        reasons.append("🎄 年末，机构做市值行情")
        score += 10
    
    # 确定信号等级
    if score >= 50:
        signal = "green"
        advice = "🟢 可买入"
    elif score >= 30:
        signal = "yellow"
        advice = "🟡 观望"
    else:
        signal = "red"
        if not reasons:
            reasons.append("😴 当前无明显买入信号，建议观望")
        advice = "🔴 不建议（等待更好买点）"
    
    return {
        "signal": signal,
        "score": score,
        "reasons": reasons,
        "advice": advice,
    }


def build_stock_data(hs300_data):
    """构建所有股票的数据"""
    logger.info("📊 开始获取15只股票数据...")
    categories = {"银行": [], "能源通信": [], "保险": [], "成长股": []}
    summary = {"total_stocks": 15, "green_count": 0, "yellow_count": 0, "red_count": 0}
    
    for idx, (code, info) in enumerate(STOCK_MAP.items()):
        name = info["name"]
        category = info["category"]
        ticker = info["ticker"]
        
        # 从 JSON 文件读取前瞻分红数据
        dividend_data = FORWARD_DIVIDEND_DATA.get(code, {})
        forward_dividend = dividend_data.get("total_dividend_per_share", 0)
        
        logger.info(f"   🔍 处理 {code} {name} ({idx+1}/15)...")
        
        # 获取股票数据（传递预计分红）
        stock_info = get_stock_data(ticker, name, forward_dividend)
        
        if stock_info is None:
            stock_info = {
                "price": None, "change_pct": None, "pe": None, "pb": None,
                "forward_dividend_yield": None, "market_cap": None,
                "error": "数据获取失败"
            }
        
        # 组装最终数据
        stock_data = {
            "code": code,
            "name": name,
            "category": category,
            "price": stock_info.get("price"),
            "change_pct": stock_info.get("change_pct"),
            "forward_dividend": forward_dividend,  # 当年每股分红
            "pe": stock_info.get("pe"),
            "pb": stock_info.get("pb"),
            "pev": stock_info.get("pb"),  # 保险股的PEV用PB近似
            "dividend_yield": stock_info.get("forward_dividend_yield"),  # 只显示前瞻股息率
            "dividend_yield_target": DIVIDEND_TARGET.get(code, 0),
            "market_cap": stock_info.get("market_cap"),
        }
        
        # 计算买入信号
        signal_result = calculate_buy_signals(stock_data, hs300_data)
        stock_data.update(signal_result)
        
        # 分类归档
        if category == "银行":
            categories["银行"].append(stock_data)
        elif category == "能源通信":
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
        if idx < len(STOCK_MAP) - 1:
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
    
    # 获取所有股票数据
    categories, summary = build_stock_data(hs300_data)
    
    # 输出 data.json（确保 JSON 可序列化）
    output = {
        "update_time": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S (北京时间)"),
        "data_date": datetime.date.today().strftime("%Y-%m-%d"),
        "hs300": convert_to_json_serializable(hs300_data),
        "categories": convert_to_json_serializable(categories),
        "summary": convert_to_json_serializable(summary),
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
