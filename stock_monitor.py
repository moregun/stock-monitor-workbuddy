#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股15只赚钱天团股票监测面板
数据源：akshare（东方财富 + 腾讯财经备用）
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
import threading

# 绕过代理（避免公司网络/代理导致 akshare 请求失败）
os.environ['NO_PROXY'] = '*'
os.environ.pop('HTTP_PROXY', None)
os.environ.pop('HTTPS_PROXY', None)
os.environ.pop('http_proxy', None)
os.environ.pop('https_proxy', None)

# ============================================================
# 超时控制：让 API 调用在指定秒数后放弃
# ============================================================
def call_with_timeout(func, timeout_sec=20):
    """
    在线程中运行 func，timeout_sec 秒后放弃等待。
    返回 func 的结果，或 None（超时/异常）。
    """
    result = [None]
    exc = [None]

    def worker():
        try:
            result[0] = func()
        except Exception as e:
            exc[0] = e

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout=timeout_sec)

    if t.is_alive():
        print(f"   ⚠️  请求超时（>{timeout_sec}s），放弃")
        return None
    if exc[0] is not None:
        print(f"   ❌ 请求异常: {exc[0]}")
        return None
    return result[0]

# ============================================================
# 一、股票清单（固定15只）
# ============================================================
STOCK_LIST = [
    {"code": "600941", "name": "中国移动", "dividend_yield_target": 7.85, "category": "通信"},
    {"code": "600938", "name": "中国海油", "dividend_yield_target": 6.28, "category": "能源"},
    {"code": "601328", "name": "交通银行", "dividend_yield_target": 5.62, "category": "银行"},
    {"code": "601166", "name": "兴业银行", "dividend_yield_target": 5.21, "category": "银行"},
    {"code": "601998", "name": "中信银行", "dividend_yield_target": 5.17, "category": "银行"},
    {"code": "601857", "name": "中国石油", "dividend_yield_target": 4.83, "category": "能源"},
    {"code": "601288", "name": "农业银行", "dividend_yield_target": 4.61, "category": "银行"},
    {"code": "601398", "name": "工商银行", "dividend_yield_target": 4.22, "category": "银行"},
    {"code": "601939", "name": "建设银行", "dividend_yield_target": 4.05, "category": "银行"},
    {"code": "601988", "name": "中国银行", "dividend_yield_target": 3.97, "category": "银行"},
    {"code": "601318", "name": "中国平安", "dividend_yield_target": 3.86, "category": "保险"},
    {"code": "601658", "name": "邮储银行", "dividend_yield_target": 3.43, "category": "银行"},
    {"code": "601628", "name": "中国人寿", "dividend_yield_target": 3.02, "category": "保险"},
    {"code": "600036", "name": "招商银行", "dividend_yield_target": 2.85, "category": "银行"},
    {"code": "300750", "name": "宁德时代", "dividend_yield_target": 0.41, "category": "成长股"},
]

# 板块分类（用于前端展示）
CATEGORY_GROUPS = {
    "银行": ["601328", "601166", "601998", "601288", "601398", "601939", "601988", "601658", "600036"],
    "能源通信": ["600941", "600938", "601857"],
    "保险": ["601318", "601628"],
    "成长股": ["300750"],
}

# ============================================================
# 二、工具函数
# ============================================================

def safe_float(val, default=None):
    try:
        if val is None or val == "" or str(val).strip() in ["-", "None", "nan", "NaN"]:
            return default
        return float(val)
    except (ValueError, TypeError):
        return default


def retry_call(func, max_retries=3, sleep_sec=2):
    """带重试的 API 调用"""
    for i in range(max_retries):
        try:
            result = func()
            return result
        except Exception as e:
            print(f"   ⚠️  第{i+1}次重试失败: {e}")
            if i < max_retries - 1:
                time.sleep(sleep_sec)
            else:
                print(f"   ❌ 已重试 {max_retries} 次，放弃")
                return None


def get_realtime_data():
    """
    获取全部A股实时行情（东方财富）
    在 GitHub Actions（境外）环境中直接返回 None，跳过主方案
    本地环境带超时控制（20秒），失败时使用逐只股票备用方案
    """
    # 检测是否在 GitHub Actions 环境中运行（境外服务器无法访问东方财富API）
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        print("   📡 检测到 GitHub Actions 环境（境外），跳过方案1，直接使用备用方案")
        return None

    print("📡 方案1：正在获取A股实时行情（东方财富）...")
    df = call_with_timeout(lambda: ak.stock_zh_a_spot_em(), timeout_sec=20)
    if df is not None and not df.empty:
        print(f"   ✅ 获取到 {len(df)} 只股票实时数据")
        return df
    print("   ❌ 方案1失败（超时或异常），将使用逐只股票备用方案")
    return None


def get_stock_by_hist(code, name):
    """
    备用方案：用 stock_zh_a_hist 获取单只股票的最新数据
    带超时控制（15秒），避免在境外环境挂起
    返回：dict with price, change_pct or None
    """
    def _fetch():
        today = datetime.date.today()
        start = (today - datetime.timedelta(days=5)).strftime("%Y%m%d")
        end = today.strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, end_date=end, adjust="qfq")
        if df is None or df.empty:
            return None
        latest = df.iloc[-1]
        # 用 收盘/开盘 估算涨跌幅
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
    
    result = call_with_timeout(lambda: _fetch(), timeout_sec=15)
    if result is None:
        print(f"   ⚠️  {code} {name} 备用方案超时或失败")
    return result


def get_hs300_by_hist():
    """
    备用方案：用 stock_zh_index_daily_em 获取沪深300最新数据
    这个接口使用不同数据源，在境外服务器上更可靠
    """
    try:
        today = datetime.date.today()
        start_date = (today - datetime.timedelta(days=5)).strftime("%Y%m%d")
        end_date = today.strftime("%Y%m%d")
        df = ak.stock_zh_index_daily_em(symbol="000300", start_date=start_date, end_date=end_date)
        if df is None or df.empty:
            return None
        latest = df.iloc[-1]
        # 兼容不同列名
        close_val = safe_float(latest.get("收盘") or latest.get("close") or latest.get("指数收盘"))
        open_val = safe_float(latest.get("开盘") or latest.get("open") or latest.get("指数开盘"))
        vol_raw = safe_float(latest.get("成交额") or latest.get("volume"))
        chg = round((close_val - open_val) / open_val * 100, 2) if close_val and open_val else None
        result = {
            "price": close_val,
            "change_pct": chg,
            "volume": round(vol_raw / 100000000, 2) if vol_raw else None,
        }
        print(f"   ✅ [备用] 沪深300 最新价={result['price']}, 涨跌幅={result['change_pct']}%")
        return result
    except Exception as e:
        print(f"   ⚠️  沪深300 历史备用方案失败: {e}")
        return None


def get_hs300_data():
    """
    获取沪深300指数实时行情 + 历史数据
    优先用 stock_zh_index_spot_em（带超时），失败则用历史数据备用
    """
    print("📡 正在获取沪深300指数数据...")
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

    # —— 步骤1：尝试获取实时行情（带超时）——
    try:
        df_index = call_with_timeout(lambda: ak.stock_zh_index_spot_em(), timeout_sec=20)
        if df_index is not None and not df_index.empty:
            hs300_row = df_index[df_index["代码"] == "000300"]
            if not hs300_row.empty:
                row = hs300_row.iloc[0]
                result["change_pct"] = safe_float(row.get("涨跌幅"))
                result["price"] = safe_float(row.get("最新价"))
                vol_raw = safe_float(row.get("成交额"))
                result["volume"] = round(vol_raw / 100000000, 2) if vol_raw else None
                print(f"   ✅ 沪深300 最新价={result['price']}, 涨跌幅={result['change_pct']}%, 成交额={result['volume']}亿")
    except Exception as e:
        print(f"   ⚠️  实时行情获取异常: {e}")

    # —— 步骤2：实时行情失败，用历史数据补最新价 ——
    if result["price"] is None:
        hist_rt = get_hs300_by_hist()
        if hist_rt:
            result["price"] = hist_rt.get("price")
            result["change_pct"] = hist_rt.get("change_pct")
            if result["volume"] is None:
                result["volume"] = hist_rt.get("volume")
            print(f"   ✅ [备用] 沪深300 最新价={result['price']}, 涨跌幅={result['change_pct']}%")

    # —— 步骤3：获取近30日历史数据（计算成交量均线和恐慌信号）——
    try:
        today = datetime.date.today()
        start_date = (today - datetime.timedelta(days=35)).strftime("%Y%m%d")
        end_date = today.strftime("%Y%m%d")
        df_hist = ak.stock_zh_index_daily_em(symbol="000300", start_date=start_date, end_date=end_date)
        if not df_hist.empty:
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
            latest_change = safe_float(latest_row.get(chg_col))

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
                print(f"   ✅ 沪深300 成交量萎缩={result['volume_shrink']}")

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
        print(f"   ⚠️  获取沪深300历史数据失败（不影响主功能）: {e}")

    return result


def calculate_buy_signals(stock_data, hs300_data):
    """计算4大买入窗口信号"""
    signals = {}
    today = datetime.date.today()
    month = today.month

    for code, data in stock_data.items():
        category = data.get("category", "")
        pe = data.get("pe")
        pb = data.get("pb")
        dividend_yield = data.get("dividend_yield_real")
        pev = data.get("pev")
        signal = "yellow"
        reasons = []
        score = 0

        # 窗口1：大盘恐慌大跌
        if hs300_data.get("panic_signal"):
            if category in ["银行", "能源", "通信", "保险"]:
                score += 50
                reasons.append("🔥 大盘恐慌大跌！黄金买点，适合分批抄底")

        # 窗口2：年底建仓期
        if month in [10, 11, 12]:
            score += 20
            reasons.append("📅 年底建仓期（10-12月），高股息股布局好时机")

        # 窗口3：分红除权前
        if category in ["银行", "能源", "通信"]:
            if month in [4, 5]:
                score += 15
                reasons.append("🎁 临近分红季（6-7月），现在布局可吃分红")
        if code == "300750":
            if month in [3, 4]:
                score += 15
                reasons.append("🎁 宁德时代临近分红季（5-6月），可提前布局")

        # 窗口4：估值安全区间
        if category == "银行":
            pe_ok = (pe is not None and pe > 0 and pe < 5)
            pb_ok = (pb is not None and pb > 0 and pb < 0.65)
            if pe_ok and pb_ok:
                score += 30
                reasons.append(f"💎 银行估值安全区：PE={pe:.2f}＜5 且 PB={pb:.2f}＜0.65，无脑买入线")
            elif pe_ok:
                score += 10
                reasons.append(f"📊 PE={pe:.2f}＜5，估值较低，可关注")
            elif pb_ok:
                score += 10
                reasons.append(f"📊 PB={pb:.2f}＜0.65，破净较深，可关注")
        elif category in ["能源", "通信"]:
            if dividend_yield is not None and dividend_yield > 6.0:
                score += 30
                reasons.append(f"💰 股息率 {dividend_yield:.2f}%＞6%，黄金坑！")
            elif dividend_yield is not None and dividend_yield > 4.0:
                score += 10
                reasons.append(f"💰 股息率 {dividend_yield:.2f}%，收益不错")
        elif category == "保险":
            if pev is not None and pev > 0 and pev < 0.7:
                score += 30
                reasons.append(f"💎 保险PEV={pev:.2f}＜0.7，安全买入线")
            elif pe is not None and pe > 0 and pe < 10:
                score += 10
                reasons.append(f"📊 保险PE={pe:.2f}，估值偏低，可关注")
        elif category == "成长股":
            if pe is not None and pe > 0 and pe < 15:
                score += 30
                reasons.append(f"🚀 宁德时代PE={pe:.2f}＜15，波段买入机会")
            elif pe is not None and pe > 0 and pe < 25:
                score += 10
                reasons.append(f"📊 宁德时代PE={pe:.2f}，估值合理，可关注")

        if score >= 50:
            signal = "green"
        elif score >= 20:
            signal = "yellow"
        else:
            signal = "red"
            if not reasons:
                reasons.append("😴 当前无明显买入信号，建议观望")

        signals[code] = {
            "signal": signal,
            "score": score,
            "reasons": reasons,
            "advice": get_advice_text(signal, category, code),
        }

    return signals


def get_advice_text(signal, category, code):
    if signal == "green":
        if category == "成长股":
            return "🚀 波段买入"
        elif category in ["能源", "通信"]:
            return "💰 黄金坑！分批买入"
        else:
            return "✅ 分批买入"
    elif signal == "yellow":
        return "⚠️ 观望（可小仓位关注）"
    else:
        return "🔴 不建议（等待更好买点）"


def build_stock_data(df_spot):
    """
    从 akshare 实时行情 DataFrame 中提取15只目标股票的数据
    若 df_spot 为 None，则逐只使用备用方案获取
    """
    result = {}
    use_backup = (df_spot is None or df_spot.empty)

    if use_backup:
        print("   📡 使用备用方案：逐只获取股票数据（历史数据补全）...")

    for stock in STOCK_LIST:
        code = stock["code"]
        name = stock["name"]
        category = stock["category"]
        target_yield = stock["dividend_yield_target"]

        if not use_backup:
            # 主方案：从实时行情 DataFrame 提取
            row_df = df_spot[df_spot["代码"] == code]
            if not row_df.empty:
                row = row_df.iloc[0]
                price = safe_float(row.get("最新价"))
                change_pct = safe_float(row.get("涨跌幅"))
                pe = safe_float(row.get("市盈率-动态"))
                pb = safe_float(row.get("市净率"))
                dividend_yield_real = safe_float(row.get("股息率"))
                market_cap = safe_float(row.get("总市值"))
                pev = pe if category == "保险" and pe is not None else None
                result[code] = {
                    "code": code, "name": name, "category": category,
                    "price": price, "change_pct": change_pct,
                    "pe": pe, "pb": pb, "pev": pev,
                    "dividend_yield_real": dividend_yield_real,
                    "dividend_yield_target": target_yield,
                    "market_cap": market_cap, "error": None,
                }
                print(f"   ✅ {code} {name}: 股价={price}, PE={pe}, PB={pb}, 股息率={dividend_yield_real}%")
                continue
            else:
                print(f"   ⚠️  {code} {name} 在主方案中未找到，切换到备用方案")

        # 备用方案：逐只获取
        backup_data = get_stock_by_hist(code, name)
        if backup_data:
            pev = backup_data.get("pe") if category == "保险" else None
            result[code] = {
                "code": code, "name": name, "category": category,
                "price": backup_data.get("price"),
                "change_pct": backup_data.get("change_pct"),
                "pe": backup_data.get("pe"),
                "pb": backup_data.get("pb"),
                "pev": pev,
                "dividend_yield_real": backup_data.get("dividend_yield"),
                "dividend_yield_target": target_yield,
                "market_cap": backup_data.get("market_cap"),
                "error": None,
            }
            print(f"   ✅ [备用] {code} {name}: 股价={backup_data.get('price')}, 涨跌幅={backup_data.get('change_pct')}%")
        else:
            print(f"   ❌ {code} {name} 备用方案也失败，填入空数据")
            result[code] = {
                "code": code, "name": name, "category": category,
                "price": None, "change_pct": None, "pe": None, "pb": None,
                "pev": None, "dividend_yield_real": None,
                "dividend_yield_target": target_yield,
                "market_cap": None, "error": "数据获取失败",
            }

    return result


def generate_output_json(stock_data, hs300_data):
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    categorized = {}
    for group_name, codes in CATEGORY_GROUPS.items():
        categorized[group_name] = []
        for code in codes:
            if code in stock_data:
                categorized[group_name].append(dict(stock_data[code]))

    signals = calculate_buy_signals(stock_data, hs300_data)

    for group_name, stocks in categorized.items():
        for i, stock in enumerate(stocks):
            code = stock["code"]
            if code in signals:
                stock["signal"] = signals[code]["signal"]
                stock["score"] = signals[code]["score"]
                stock["reasons"] = signals[code]["reasons"]
                stock["advice"] = signals[code]["advice"]
            else:
                stock["signal"] = "red"
                stock["score"] = 0
                stock["reasons"] = ["数据不足，建议观望"]
                stock["advice"] = "🔴 数据不足"

    output = {
        "update_time": now_str,
        "data_date": today_str,
        "hs300": {
            "price": hs300_data.get("price"),
            "change_pct": hs300_data.get("change_pct"),
            "volume": hs300_data.get("volume"),
            "volume_20d_avg": hs300_data.get("volume_20d_avg"),
            "volume_shrink": hs300_data.get("volume_shrink", False),
            "volume_shrink_reason": hs300_data.get("volume_shrink_reason", ""),
            "panic_signal": hs300_data.get("panic_signal", False),
            "panic_reason": hs300_data.get("panic_reason", ""),
        },
        "categories": categorized,
        "summary": {
            "total_stocks": len(STOCK_LIST),
            "green_count": sum(1 for s in signals.values() if s["signal"] == "green"),
            "yellow_count": sum(1 for s in signals.values() if s["signal"] == "yellow"),
            "red_count": sum(1 for s in signals.values() if s["signal"] == "red"),
        }
    }
    return output


def main():
    print("=" * 60)
    print("  A股15只赚钱天团股票监测 - 数据抓取")
    print("   数据源：akshare（东方财富 + 历史数据备用）")
    print("=" * 60)

    # 1. 获取沪深300大盘数据
    hs300_data = get_hs300_data()

    # 2. 获取全部A股实时行情（失败会自动切换备用方案）
    print("\n📊 提取15只目标股票数据...")
    df_spot = get_realtime_data()
    stock_data = build_stock_data(df_spot)

    # 3. 生成 data.json
    print("\n📝 生成 data.json...")
    output = generate_output_json(stock_data, hs300_data)

    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"   ✅ 数据已写入：{output_path}")

    # 打印摘要
    print("\n" + "=" * 60)
    print("  📊 本次监测摘要")
    print("=" * 60)
    hs300 = output["hs300"]
    print(f"  沪深300：{hs300['price']} 点，涨跌幅 {hs300['change_pct']}%")
    print(f"  当日成交额：{hs300['volume']} 亿")
    print(f"  近20日均值：{hs300['volume_20d_avg']} 亿")
    print(f"  成交量萎缩：{'🔥 是！' if hs300['volume_shrink'] else '❌ 否'}")
    print(f"  恐慌信号：{'🔥 是！黄金买点' if hs300['panic_signal'] else '否'}")
    if hs300['panic_reason']:
        print(f"  说明：{hs300['panic_reason']}")
    print(f"\n  买入信号分布：")
    print(f"    🟢 可买入（绿）：{output['summary']['green_count']} 只")
    print(f"    🟡 观望（黄）：{output['summary']['yellow_count']} 只")
    print(f"    🔴 不建议（红）：{output['summary']['red_count']} 只")
    print(f"\n  更新时间：{output['update_time']}")
    print("=" * 60)
    print("✅ 完成！请将 data.json 提交到 GitHub 仓库，HTML 面板即可读取。")


if __name__ == "__main__":
    main()
