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
        
        # 检查是否已有2025年报数据
        dividends = stock_info.get('dividends', [])
        has_2025_annual = any(d.get('报告时间') == '2025年报' for d in dividends)
        
        if has_2025_annual:
            print(f"  ✅ 已有2025年报数据，跳过")
            print()
            continue
        
        # 抓取2025年报分红数据
        dividend_data = fetch_2025_dividend(stock_code, stock_name)
        
        if dividend_data:
            # 添加新分红数据
            stock_info['dividends'].append({
                '报告时间': dividend_data['report_time'],
                '分红说明': dividend_data['dividend_text'],
                '每股分红': dividend_data['dividend_per_share']
            })
            
            # 重新计算 total_dividend_per_share
            total = sum([d['每股分红'] for d in stock_info['dividends']])
            stock_info['total_dividend_per_share'] = round(total, 4)
            stock_info['forward_dividend_yield_base'] = round(total, 4)
            
            # 删除 note 字段（如果有）
            if 'note' in stock_info:
                del stock_info['note']
            
            print(f"  ✅ 已更新 {stock_name}")
            print(f"      总分红: {total:.4f} 元/股")
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
