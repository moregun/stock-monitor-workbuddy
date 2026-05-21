#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股15只赚钱天团股票监测面板
数据源：akshare（东方财富）
功能：抓取15只巨无霸股票实时数据 + 沪深300大盘数据，计算4大买入窗口信号，输出 data.json
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

# 绕过代理（避免公司网络/代理导致 akshare 请求失败）
os.environ['NO_PROXY'] = '*'
os.environ.pop('HTTP_PROXY', None)
os.environ.pop('HTTPS_PROXY', None)
os.environ.pop('http_proxy', None)
os.environ.pop('https_proxy', None)

# ============================================================
# 一、股票清单（固定15只，严格按照用户提供的清单）
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
    """安全转换为 float，失败返回 default"""
    try:
        if val is None or val == "" or str(val).strip() in ["-", "None", "nan"]:
            return default
        return float(val)
    except (ValueError, TypeError):
        return default


def get_realtime_data():
    """
    获取全部A股实时行情（来自东方财富）
    返回：DataFrame，全部A股实时行情
    """
    print("📡 正在获取A股实时行情（东方财富）...")
    try:
        # stock_zh_a_spot_em 返回沪深京A股实时行情，字段包括：
        # 代码, 名称, 最新价, 涨跌幅, 涨跌额, 成交量, 成交额, 振幅,
        # 最高, 最低, 今开, 昨收, 换手率, 市盈率-动态, 市净率, 总市值, 流通市值, 股息率
        df = ak.stock_zh_a_spot_em()
        print(f"   ✅ 获取到 {len(df)} 只股票实时数据")
        return df
    except Exception as e:
        print(f"   ❌ 获取实时行情失败: {e}")
        return None


def get_hs300_data():
    """
    获取沪深300指数实时行情 + 历史数据（用于判断成交量和跌幅）
    返回：dict，包含 hs300_change_pct, hs300_volume, hs300_volume_5d_avg
    """
    print("📡 正在获取沪深300指数数据...")
    result = {
        "hs300_change_pct": None,
        "hs300_price": None,
        "hs300_volume": None,
        "hs300_volume_prev": None,
        "panic_signal": False,
        "panic_reason": "",
    }
    try:
        # 获取指数实时行情（东方财富）
        df_index = ak.stock_zh_index_spot_em()
        # 找到沪深300（代码 000300）
        hs300_row = df_index[df_index["代码"] == "000300"]
        if not hs300_row.empty:
            row = hs300_row.iloc[0]
            result["hs300_change_pct"] = safe_float(row.get("涨跌幅"))
            result["hs300_price"] = safe_float(row.get("最新价"))
            # 成交额（手）→ 成交量
            result["hs300_volume"] = safe_float(row.get("成交额"))
            print(f"   ✅ 沪深300 最新价={result['hs300_price']}, 涨跌幅={result['hs300_change_pct']}%")

        # 获取近5日沪深300历史数据，计算成交量均线
        try:
            today = datetime.date.today()
            start_date = (today - datetime.timedelta(days=14)).strftime("%Y%m%d")
            end_date = today.strftime("%Y%m%d")
            df_hist = ak.stock_zh_index_daily_em(symbol="000300", start_date=start_date, end_date=end_date)
            if not df_hist.empty:
                # 取最近5个交易日（排除今天可能的不完整数据）
                recent = df_hist.tail(6).head(5)  # 最近5天（不含今天）
                if len(recent) > 0:
                    vol_col = "成交量" if "成交量" in recent.columns else recent.columns[-2]
                    vol_avg = recent[vol_col].astype(float).mean()
                    result["hs300_volume_5d_avg"] = vol_avg
                    print(f"   ✅ 沪深300 近5日平均成交量={vol_avg:.0f}")
        except Exception as e:
            print(f"   ⚠️  获取沪深300历史数据失败（不影响主功能）: {e}")

        # 判断恐慌信号：单日跌幅 > 2.5% 且成交量萎缩
        if result["hs300_change_pct"] is not None:
            change = result["hs300_change_pct"]
            volume = result.get("hs300_volume", None)
            vol_avg = result.get("hs300_volume_5d_avg", None)

            # 条件1：跌幅 > 2.5%
            condition1 = change < -2.5

            # 条件2：成交量萎缩（今日成交量 < 近5日平均的90%）
            condition2 = False
            if volume is not None and vol_avg is not None:
                condition2 = (volume < vol_avg * 0.9)

            result["panic_signal"] = condition1  # 主要看跌幅，成交量作为辅助
            if condition1:
                result["panic_reason"] = f"沪深300单日跌幅 {change:.2f}%（超过2.5%警戒线）"
                if condition2:
                    result["panic_reason"] += "，且成交量萎缩"
            else:
                result["panic_reason"] = f"沪深300涨跌幅 {change:.2f}%，未达恐慌线（-2.5%）"

        return result
    except Exception as e:
        print(f"   ❌ 获取沪深300数据失败: {e}")
        return result


def calculate_buy_signals(stock_data, hs300_data):
    """
    计算4大买入窗口信号
    返回：信号dict，包含 signal（green/yellow/red）、reason（大白话说明）
    """
    signals = {}
    today = datetime.date.today()
    month = today.month
    day = today.day

    for code, data in stock_data.items():
        category = data.get("category", "")
        pe = data.get("pe")
        pb = data.get("pb")
        dividend_yield = data.get("dividend_yield_real")  # 实时股息率（%）
        pev = data.get("pev")  # 保险PEV，若无则为None

        signal = "yellow"  # 默认观望
        reasons = []
        score = 0  # 信号得分，越高越值得买

        # ---------- 窗口1：大盘恐慌大跌（最高优先级）----------
        if hs300_data.get("panic_signal"):
            # 适配：银行/能源通信/保险都适合抄底
            if category in ["银行", "能源", "通信", "保险"]:
                score += 50
                reasons.append("🔥 大盘恐慌大跌！黄金买点，适合分批抄底")

        # ---------- 窗口2：每年10-12月年底建仓期 ----------
        if month in [10, 11, 12]:
            score += 20
            reasons.append("📅 年底建仓期（10-12月），高股息股布局好时机")

        # ---------- 窗口3：分红除权前1-2个月 ----------
        # 规律：银行/能源通信 6-7月分红 → 4-5月买入；宁德时代 5-6月分红 → 3-4月买入
        if category in ["银行", "能源", "通信"]:
            if month in [4, 5]:
                score += 15
                reasons.append("🎁 临近分红季（6-7月），现在布局可吃分红")
        if code == "300750":
            if month in [3, 4]:
                score += 15
                reasons.append("🎁 宁德时代临近分红季（5-6月），可提前布局")

        # ---------- 窗口4：估值/股息率安全区间 ----------
        if category == "银行":
            # 银行：PE＜5倍 且 PB＜0.65倍 → 安全买入
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
            # 能源通信：实时股息率＞6% → 黄金坑
            if dividend_yield is not None and dividend_yield > 6.0:
                score += 30
                reasons.append(f"💰 股息率 {dividend_yield:.2f}%＞6%，黄金坑！")
            elif dividend_yield is not None and dividend_yield > 4.0:
                score += 10
                reasons.append(f"💰 股息率 {dividend_yield:.2f}%，收益不错")

        elif category == "保险":
            # 保险：PEV＜0.7倍 → 安全买入（若无法获取PEV，用PE替代参考）
            if pev is not None and pev > 0 and pev < 0.7:
                score += 30
                reasons.append(f"💎 保险PEV={pev:.2f}＜0.7，安全买入线")
            elif pe is not None and pe > 0 and pe < 10:
                score += 10
                reasons.append(f"📊 保险PE={pe:.2f}，估值偏低，可关注")

        elif category == "成长股":
            # 宁德时代：PE＜15倍 → 波段买入
            if pe is not None and pe > 0 and pe < 15:
                score += 30
                reasons.append(f"🚀 宁德时代PE={pe:.2f}＜15，波段买入机会")
            elif pe is not None and pe > 0 and pe < 25:
                score += 10
                reasons.append(f"📊 宁德时代PE={pe:.2f}，估值合理，可关注")

        # ---------- 综合判断信号 ----------
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
    """根据信号生成大白话操作建议"""
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
    返回：dict，key=代码，value=股票数据dict
    """
    result = {}
    if df_spot is None:
        print("   ⚠️  实时行情数据为空，无法提取股票数据")
        return result

    for stock in STOCK_LIST:
        code = stock["code"]
        name = stock["name"]
        category = stock["category"]
        target_yield = stock["dividend_yield_target"]

        # 在实时行情中查找该股票
        row_df = df_spot[df_spot["代码"] == code]

        if row_df.empty:
            print(f"   ⚠️  未找到股票：{code} {name}")
            # 填入空数据（避免前端报错）
            result[code] = {
                "code": code,
                "name": name,
                "category": category,
                "price": None,
                "change_pct": None,
                "pe": None,
                "pb": None,
                "pev": None,
                "dividend_yield_real": None,
                "dividend_yield_target": target_yield,
                "market_cap": None,
                "error": "未获取到数据",
            }
            continue

        row = row_df.iloc[0]

        # 提取字段（akshare stock_zh_a_spot_em 返回的列名）
        price = safe_float(row.get("最新价"))
        change_pct = safe_float(row.get("涨跌幅"))
        pe = safe_float(row.get("市盈率-动态"))
        pb = safe_float(row.get("市净率"))
        dividend_yield_real = safe_float(row.get("股息率"))
        market_cap = safe_float(row.get("总市值"))

        # 保险股PEV（价格内含价值比）需要从其他接口获取，此处先用PE替代参考
        pev = None
        if category == "保险":
            # 尝试用PE粗略替代PEV判断（PE较低时通常PEV也较低）
            pev = pe

        result[code] = {
            "code": code,
            "name": name,
            "category": category,
            "price": price,
            "change_pct": change_pct,
            "pe": pe,
            "pb": pb,
            "pev": pev,
            "dividend_yield_real": dividend_yield_real,
            "dividend_yield_target": target_yield,
            "market_cap": market_cap,
            "error": None,
        }
        print(f"   ✅ {code} {name}: 股价={price}, 涨跌幅={change_pct}%, PE={pe}, PB={pb}, 股息率={dividend_yield_real}%")

    return result


def generate_output_json(stock_data, hs300_data):
    """
    生成最终输出的 data.json
    """
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 按板块分组股票数据
    categorized = {}
    for group_name, codes in CATEGORY_GROUPS.items():
        categorized[group_name] = []
        for code in codes:
            if code in stock_data:
                item = dict(stock_data[code])
                # 加入买入信号（稍后填充）
                categorized[group_name].append(item)

    # 计算买入信号
    signals = calculate_buy_signals(stock_data, hs300_data)

    # 将信号注入各板块数据
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

    # 构建输出
    output = {
        "update_time": now_str,
        "data_date": today_str,
        "hs300": {
            "price": hs300_data.get("hs300_price"),
            "change_pct": hs300_data.get("hs300_change_pct"),
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
    print("  A股巨无霸高股息监测系统 - 数据抓取")
    print("  数据源：akshare（东方财富）")
    print("=" * 60)

    # 1. 获取沪深300大盘数据
    hs300_data = get_hs300_data()

    # 2. 获取全部A股实时行情
    df_spot = get_realtime_data()

    # 3. 提取15只目标股票的数据
    print("\n📊 提取15只目标股票数据...")
    stock_data = build_stock_data(df_spot)

    # 4. 生成 data.json
    print("\n📝 生成 data.json...")
    output = generate_output_json(stock_data, hs300_data)

    # 写入文件
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
