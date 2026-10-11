#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从同花顺网页抓取2025年报分红方案
URL格式: https://basic.10jqka.com.cn/股票代码/bonus.html

使用方法:
    python fetch_10jqka_dividend.py
"""

import re
import json
import time
import requests
from bs4 import BeautifulSoup

# 报告期归一化：这些后缀代表同一笔分红的中间状态，去重时应视为同一报告期
REPORT_SUFFIXES = ("预案", "实施", "预披露", "董事会", "股东大会")


def canonical_report(report_time):
    """归一化报告期：去掉预案/实施等后缀，如 '2025年报预案' -> '2025年报'"""
    if not report_time:
        return ""
    rt = str(report_time)
    for suffix in REPORT_SUFFIXES:
        rt = rt.replace(suffix, "")
    return rt.strip()


def is_proposal(report_time):
    """判断是否为预案（预案应被正式年报/实施取代，不重复累计）"""
    return "预案" in str(report_time or "")


def dedupe_dividends(dividends):
    """
    按报告期去重，修复分红重复累计：
    - 同一报告期下同时存在预案与正式条目时，保留正式、丢弃预案（预案会被正式取代，二者是同一笔分红）；
    - 同一报告期下有多条非预案条目（如年报 + 特别分红），如实保留，不合并不同金额。
    返回去重后的列表。
    """
    groups = {}
    order = []
    for d in dividends:
        key = canonical_report(d.get("报告时间"))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(d)

    cleaned = []
    for key in order:
        entries = groups[key]
        formal = [d for d in entries if not is_proposal(d.get("报告时间"))]
        if len(entries) > 1 and formal:
            # 同报告期同时含预案与正式：丢弃预案，保留全部正式条目
            cleaned.extend(formal)
        else:
            cleaned.extend(entries)
    return cleaned


def fiscal_year_total(dividends):
    """
    按「最新财年」汇总前瞻分红，修复把往年年报重复计入的问题。
    只汇总最新年份所有报告期（如 2025半年报 + 2025年报 = 2025 全年分红），
    排除更早年份记录（如 2024年报）。无法识别年份时退回对全部记录求和（兼容旧数据）。
    """
    if not dividends:
        return 0.0
    year_map = {}
    for d in dividends:
        m = re.search(r"(20\d{2})", str(d.get("报告时间") or ""))
        year = m.group(1) if m else None
        year_map.setdefault(year, []).append(d)
    years = [y for y in year_map if y]
    if not years:
        return float(sum(float(d["每股分红"]) for d in dividends))
    latest_year = max(years)
    return float(sum(float(d["每股分红"]) for d in year_map[latest_year]))

# 读取 forward_dividend.json 获取股票列表
def load_stock_list():
    """从 forward_dividend.json 读取股票列表"""
    try:
        with open('forward_dividend.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data
    except Exception as e:
        print(f"❌ 读取 forward_dividend.json 失败: {e}")
        return None


def fetch_2025_dividend(stock_code, stock_name):
    """
    从同花顺抓取2025年报分红方案
    返回: {
        'report_time': '2025年报',
        'dividend_text': '10派1.684元(含税)',
        'dividend_per_share': 0.1684
    } 或 None
    """
    url = f"https://basic.10jqka.com.cn/{stock_code}/bonus.html"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }
    
    try:
        print(f"  🔍 正在抓取 {stock_name}({stock_code}) 的分红数据...")
        response = requests.get(url, headers=headers, timeout=15)
        response.encoding = 'gbk'  # 同花顺使用 GBK 编码
        
        if response.status_code != 200:
            print(f"    ⚠️  HTTP {response.status_code}")
            return None
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 查找所有表格
        tables = soup.find_all('table')
        
        for table_idx, table in enumerate(tables):
            rows = table.find_all('tr')
            
            for row_idx, row in enumerate(rows):
                cells = row.find_all(['td', 'th'])
                if len(cells) < 3:
                    continue
                
                # 提取整行文本
                row_text = ' | '.join([cell.get_text(strip=True) for cell in cells])
                
                # 查找包含 "2025年报" 和 "派" 的行
                if '2025年报' in row_text and '派' in row_text:
                    print(f"    ✅ 找到2025年报分红数据 (表格{table_idx} 行{row_idx})")
                    
                    # 提取分红方案说明
                    # 格式: 10派X元(含税) 或 10派X元
                    match = re.search(r'(10派\d+\.?\d*元(?:\(含税\))?)', row_text)
                    if match:
                        dividend_text = match.group(1)
                        # 提取每股分红金额
                        amount_match = re.search(r'10派(\d+\.?\d*)元', dividend_text)
                        if amount_match:
                            dividend_per_10 = float(amount_match.group(1))
                            dividend_per_share = round(dividend_per_10 / 10, 4)
                            
                            print(f"      分红方案: {dividend_text}")
                            print(f"      每股分红: {dividend_per_share} 元")
                            
                            return {
                                'report_time': '2025年报',
                                'dividend_text': dividend_text,
                                'dividend_per_share': dividend_per_share
                            }
        
        print(f"    ⚠️  未找到2025年报分红数据")
        return None
        
    except Exception as e:
        print(f"    ❌ 抓取失败: {e}")
        return None
    
    finally:
        time.sleep(2)  # 避免请求过快


def update_forward_dividend_json():
    """
    更新 forward_dividend.json 文件
    为缺失2025年报分红的股票添加数据
    """
    data = load_stock_list()
    if not data:
        return
    
    print("=" * 70)
    print("🚀 从同花顺抓取2025年报分红方案")
    print("=" * 70)
    print()
    
    updated_count = 0
    
    for stock_code, stock_info in data.items():
        stock_name = stock_info.get('name', stock_code)
        
        print(f"处理 {stock_code} {stock_name}")

        # 先对已有分红做一次去重（修复历史预案/年报重复累计）
        dividends = dedupe_dividends(stock_info.get('dividends', []))

        # 检查是否已有2025年报数据（预案也视为已有，避免重复抓取/重复累计）
        has_2025_annual = any(canonical_report(d.get('报告时间')) == '2025年报' for d in dividends)

        if has_2025_annual:
            stock_info['dividends'] = dividends
            print(f"  ✅ 已有2025年报数据，跳过")
            print()
            continue

        # 抓取2025年报分红数据
        dividend_data = fetch_2025_dividend(stock_code, stock_name)

        if dividend_data:
            new_report = dividend_data['report_time']  # '2025年报'
            new_canon = canonical_report(new_report)
            new_entry = {
                '报告时间': new_report,
                '分红说明': dividend_data['dividend_text'],
                '每股分红': dividend_data['dividend_per_share']
            }
            # 替换同报告期的旧条目（预案/旧数据），而非追加，避免重复累计
            dividends = [d for d in dividends if canonical_report(d.get('报告时间')) != new_canon]
            dividends.append(new_entry)
            stock_info['dividends'] = dedupe_dividends(dividends)

            # 重新计算 total_dividend_per_share（按最新财年汇总，排除往年年报）
            total = round(fiscal_year_total(stock_info['dividends']), 4)
            stock_info['total_dividend_per_share'] = total
            stock_info['forward_dividend_yield_base'] = total

            # 删除 note 字段（如果有）
            if 'note' in stock_info:
                del stock_info['note']

            print(f"  ✅ 已更新 {stock_name}")
            print(f"      总分红(最新财年): {total:.4f} 元/股（去重后 {len(stock_info['dividends'])} 条）")
            print()

            updated_count += 1
        else:
            print(f"  ⚠️  未能获取2025年报分红数据")
            print()
        
        # 每只股票请求后延迟2秒
        if stock_code != list(data.keys())[-1]:
            time.sleep(2)
    
    # 保存更新后的数据
    if updated_count > 0:
        try:
            with open('forward_dividend.json', 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print("=" * 70)
            print(f"✅ forward_dividend.json 已更新 (共更新 {updated_count} 只股票)")
            print("=" * 70)
        except Exception as e:
            print(f"❌ 保存 forward_dividend.json 失败: {e}")
    else:
        print("=" * 70)
        print("⚠️  没有更新任何数据")
        print("=" * 70)


if __name__ == "__main__":
    update_forward_dividend_json()
