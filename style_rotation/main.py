# -*- coding: utf-8 -*-
"""
创业板 / 中证红利 风格轮动监控工具
====================================
指标：ratio = 创业板指(399006) / 中证红利(000922)
阈值（黄金分割改进版）：
    ratio >= 0.618  -> 预警：成长高估，减仓创业板，转向红利
    ratio <= 0.382  -> 机会：成长低估，布局创业板
    0.382~0.618     -> 中性：均衡配置

特性：
    1. 可直接部署 GitHub Actions 定时自动运行（免费云端监控）
    2. 获取实时指数数据，计算比值，输出信号
    3. 支持历史数据缓存（index_cache.json）、简单绘图（HTML 内联 SVG + matplotlib PNG）
    4. 结果输出日志，预留微信/邮件推送接口

⚠️ 仅量化观测工具，不构成投资建议
"""
import os
import sys
import json
import logging
import warnings
import datetime

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)

try:
    import akshare as ak
except Exception as e:  # pragma: no cover
    logger.error("❌ 未安装 akshare，请先 pip install -r requirements.txt ：%s", e)
    sys.exit(1)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAVE_MPL = True
except Exception:  # pragma: no cover
    HAVE_MPL = False

# ====================== 策略参数 ======================
INDEX_CY = "399006"        # 创业板指
INDEX_DIV = "000922"       # 中证红利
THRESHOLD_BUY = 0.382      # 下沿阈值（机会）
THRESHOLD_SELL = 0.618     # 上沿阈值（预警）
START_DATE = "20150101"
CACHE_FILE = "index_cache.json"
OUTPUT_JSON = "style_rotation_data.json"
OUTPUT_LOG = "monitor_log.txt"
OUTPUT_PNG = "ratio_chart.png"
# ======================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def out_path(name):
    return os.path.join(SCRIPT_DIR, name)


BEIJING = datetime.timezone(datetime.timedelta(hours=8))


def safe_float(val, default=None):
    try:
        if val is None:
            return default
        if isinstance(val, (np.integer, np.floating)):
            val = float(val)
        if isinstance(val, (np.bool_, bool)):
            return default
        r = float(val)
        if np.isnan(r) or np.isinf(r):
            return default
        return r
    except (ValueError, TypeError, OverflowError):
        return default


def beijing_now():
    return datetime.datetime.now(BEIJING)


def load_cache():
    try:
        p = out_path(CACHE_FILE)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_cache(cache):
    try:
        with open(out_path(CACHE_FILE), "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception as e:
        logger.warning("缓存保存失败: %s", e)


def fetch_index_window(code, start_date, end_date):
    """获取 [start_date, end_date] 区间指数日线，返回 {date_str: close}"""
    out = {}
    prefixes = ["sz"] if code == "399006" else ["sh"]
    candidates = []
    for p in prefixes:
        candidates.append(("stock_zh_index_daily", f"{p}{code}"))
        candidates.append(("stock_zh_index_daily_em", f"{p}{code}"))
    candidates.append(("index_zh_a_hist", code))

    errors = []
    for func_name, sym in candidates:
        try:
            func = getattr(ak, func_name)
            if func_name == "index_zh_a_hist":
                df = func(symbol=sym, period="daily",
                          start_date=start_date.replace("-", ""),
                          end_date=end_date.replace("-", ""))
            else:
                df = func(symbol=sym)
            if df is None or df.empty:
                errors.append(f"{func_name}:empty")
                continue
            df = df.rename(columns={"date": "date", "日期": "date",
                                    "close": "close", "收盘": "close"})
            df = df[["date", "close"]].dropna()
            for _, row in df.iterrows():
                d = pd.to_datetime(row["date"]).strftime("%Y-%m-%d")
                c = safe_float(row["close"])
                if c is not None:
                    out[d] = c
            if out:
                return out
        except Exception as e:
            errors.append(f"{func_name}:{e}")
            continue
    raise RuntimeError(" | ".join(errors))


def get_index_series(code):
    """获取指数日线序列（含历史缓存增量更新），返回 DataFrame[date, close]"""
    cache = load_cache()
    code_cache = cache.get(code, {})
    today = beijing_now().strftime("%Y-%m-%d")

    if code_cache:
        latest = max(code_cache.keys())
        start = (pd.Timestamp(latest) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        if start <= today:
            try:
                inc = fetch_index_window(code, start, today)
                code_cache.update(inc)
                logger.info("   ↺ %s 增量更新 %d 个交易日", code, len(inc))
            except Exception as e:
                logger.warning("   ⚠️ %s 增量更新失败，回退全量: %s", code, e)
                code_cache = fetch_index_window(code, START_DATE, today)
    else:
        code_cache = fetch_index_window(code, START_DATE, today)

    cutoff = pd.Timestamp(START_DATE)
    code_cache = {d: c for d, c in code_cache.items() if pd.Timestamp(d) >= cutoff}
    cache[code] = code_cache
    save_cache(cache)

    s = pd.DataFrame([(d, code_cache[d]) for d in sorted(code_cache.keys())],
                     columns=["date", "close"])
    s["date"] = pd.to_datetime(s["date"])
    return s.sort_values("date").reset_index(drop=True)


def push_notify(title, text):
    """推送通知（预留接口）。

    可在此接入：企业微信/微信推送（Server酱、pushplus、企业微信机器人 Webhook）、邮件（smtplib）。
    通过环境变量配置，例如 WXPUSH_TOKEN / PUSHPLUS_TOKEN / SMTP_*。
    当前为占位实现，不发送任何内容。
    """
    logger.info("📨 [推送预留] %s - %s", title, text)
    # TODO: 接入具体推送服务
    # if os.getenv("PUSHPLUS_TOKEN"):
    #     ...


def draw_chart(merge_df, latest_date, latest_ratio):
    if not HAVE_MPL:
        logger.warning("matplotlib 不可用，跳过 PNG 绘图")
        return
    try:
        plt.rcParams["font.sans-serif"] = ["WenQuanYi Micro Hei", "SimHei",
                                            "Microsoft YaHei", "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False
        fig, ax = plt.subplots(figsize=(14, 6))
        ax.plot(merge_df["date"], merge_df["ratio"], label="创业板/中证红利比值", color="#2563eb")
        ax.axhline(y=THRESHOLD_SELL, color="#dc2626", linestyle="--", label=f"高估阈值 {THRESHOLD_SELL}")
        ax.axhline(y=THRESHOLD_BUY, color="#16a34a", linestyle="--", label=f"低估阈值 {THRESHOLD_BUY}")
        ax.scatter(latest_date, latest_ratio, c="orange", s=60, zorder=5, label="当前位置")
        ax.set_title("创业板/中证红利 风格轮动比值监控（黄金分割阈值0.382/0.618）")
        ax.set_ylabel("比值")
        ax.legend()
        ax.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(out_path(OUTPUT_PNG), dpi=150)
        plt.close()
        logger.info("✅ 图表已保存：%s", OUTPUT_PNG)
    except Exception as e:
        logger.warning("⚠️ 绘图失败（不影响主流程）: %s", e)


def main():
    logger.info("=" * 50)
    logger.info("🔄 风格轮动监控启动 %s", beijing_now().strftime('%Y-%m-%d %H:%M:%S'))
    logger.info("=" * 50)

    s_cy = get_index_series(INDEX_CY)
    s_div = get_index_series(INDEX_DIV)
    logger.info("创业板指 %d 条 / 中证红利 %d 条日线", len(s_cy), len(s_div))

    merge_df = pd.merge(s_cy, s_div, on="date", suffixes=("_cy", "_div")).sort_values("date")
    merge_df["ratio"] = merge_df["close_cy"] / merge_df["close_div"]
    if merge_df.empty:
        logger.error("❌ 合并后无共同交易日数据，无法计算比值")
        sys.exit(1)

    latest = merge_df.iloc[-1]
    latest_date = latest["date"]
    latest_ratio = float(latest["ratio"])
    cy_close = safe_float(latest["close_cy"])
    div_close = safe_float(latest["close_div"])

    logger.info("最新交易日：%s", latest_date.date())
    logger.info("创业板指 / 中证红利 比值 = %.4f", latest_ratio)
    logger.info("阈值区间：%.3f ~ %.3f", THRESHOLD_BUY, THRESHOLD_SELL)

    if latest_ratio >= THRESHOLD_SELL:
        signal = "warning"
        signal_text = "【⚠️ 预警信号】成长相对红利高估，建议降低创业板仓位，增配中证红利"
    elif latest_ratio <= THRESHOLD_BUY:
        signal = "opportunity"
        signal_text = "【✅ 布局信号】成长相对红利低估，可逢低布局创业板"
    else:
        signal = "neutral"
        signal_text = "【➖ 中性区间】风格无明确机会，均衡配置"
    logger.info(signal_text)

    # 绘图（PNG 制品，失败不影响主流程）
    draw_chart(merge_df, latest_date, latest_ratio)

    # 历史序列（用于 HTML 内联 SVG 绘图）
    history = [
        {"date": d.strftime("%Y-%m-%d"), "ratio": round(float(r), 4)}
        for d, r in zip(merge_df["date"], merge_df["ratio"])
    ]

    output = {
        "update_time": beijing_now().strftime("%Y-%m-%d %H:%M:%S (北京时间)"),
        "data_date": latest_date.strftime("%Y-%m-%d"),
        "index_cy": {"code": INDEX_CY, "name": "创业板指", "close": round(cy_close, 2) if cy_close else None},
        "index_div": {"code": INDEX_DIV, "name": "中证红利", "close": round(div_close, 2) if div_close else None},
        "ratio": round(latest_ratio, 4),
        "buy_threshold": THRESHOLD_BUY,
        "sell_threshold": THRESHOLD_SELL,
        "signal": signal,
        "signal_text": signal_text,
        "history": history,
    }
    with open(out_path(OUTPUT_JSON), "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, allow_nan=False)
    logger.info("✅ 数据已保存：%s", OUTPUT_JSON)

    log_text = (
        "\n==== 风格轮动监控报告 ====\n"
        f"时间: {output['update_time']}\n"
        f"最新数据日期: {output['data_date']}\n"
        f"创业板指(399006)收盘: {output['index_cy']['close']}\n"
        f"中证红利(000922)收盘: {output['index_div']['close']}\n"
        f"比值: {output['ratio']:.4f}\n"
        f"低估阈值: {THRESHOLD_BUY}\n"
        f"高估阈值: {THRESHOLD_SELL}\n"
        f"交易信号: {signal_text}\n"
    )
    with open(out_path(OUTPUT_LOG), "w", encoding="utf-8") as f:
        f.write(log_text)
    logger.info(log_text)

    # 推送（预留接口）
    push_notify("风格轮动监控", f"比值={output['ratio']:.4f} | {signal_text}")


if __name__ == "__main__":
    main()
