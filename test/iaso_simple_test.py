#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IASO Fail-Slow Detection - 简化测试版本
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta

print("="*60)
print("IASO Fail-Slow Detection Framework - Test Version")
print("="*60)

DATA_FILE = 'data/a.csv'

# 参数设置
EPOCH_SECONDS = 15
LATENCY_MULTIPLIER = 2.0
MIN_TTR_MINUTES = 2.0
RATIO_THRESH = 0.1

print("\n参数设置:")
print(f"  纪元时间窗口: {EPOCH_SECONDS} 秒")
print(f"  延迟倍数阈值: {LATENCY_MULTIPLIER}")
print(f"  恢复时间: {MIN_TTR_MINUTES} 分钟")
print(f"  比率阈值: {RATIO_THRESH}")

# 加载数据
print("\n正在加载数据...")
try:
    data = pd.read_csv(DATA_FILE)
    print(f"✓ 数据加载成功: {len(data)} 行")
    
    # 转换时间戳
    data['timestamp'] = pd.to_datetime(data['timestamp'], format='%m/%d/%Y %I:%M:%S %p')
    data = data.sort_values('timestamp').reset_index(drop=True)
    
    # 处理延迟数据
    data['w_await'] = pd.to_numeric(data['w_await'], errors='coerce')
    data = data.dropna(subset=['w_await'])
    
    print(f"✓ 时间范围: {data['timestamp'].min()} 到 {data['timestamp'].max()}")
    print(f"✓ 设备数量: {data['device'].nunique()} ({', '.join(data['device'].unique())})")
    
    # 开始检测
    print("\n开始执行 IASO 检测...")
    
    start_time = data['timestamp'].min()
    end_time = data['timestamp'].max()
    
    previous_score = 1.0
    epoch_start = start_time
    epoch_count = 0
    score_db = []
    
    # 按纪元处理
    while epoch_start < end_time:
        epoch_end = epoch_start + timedelta(seconds=EPOCH_SECONDS)
        
        # 切片数据
        epoch_data = data[
            (data['timestamp'] >= epoch_start) & 
            (data['timestamp'] < epoch_end)
        ]
        
        if len(epoch_data) == 0:
            epoch_start = epoch_end
            continue
        
        # 计算基准延迟
        median_latency = epoch_data['w_await'].median()
        latency_threshold = median_latency * LATENCY_MULTIPLIER
        
        # 统计慢盘
        num_slow_drives = (epoch_data['w_await'] > latency_threshold).sum()
        total_drives = len(epoch_data)
        to_resp_ratio = num_slow_drives / total_drives if total_drives > 0 else 0.0
        
        # AIMD 算法
        if to_resp_ratio == 0:
            # 加性减少
            min_ttr_seconds = MIN_TTR_MINUTES * 60
            decrease_amount = (100 * EPOCH_SECONDS) / min_ttr_seconds
            new_score = max(1.0, previous_score - decrease_amount)
        else:
            # 乘性增加
            min_ratio = min(to_resp_ratio, RATIO_THRESH)
            near_thresh = min_ratio / RATIO_THRESH
            increase_amount = previous_score * near_thresh
            new_score = min(100.0, previous_score + increase_amount)
        
        # 存储结果
        score_db.append({
            'timestamp': epoch_end,
            'score': new_score,
            'median_latency': median_latency,
            'slow_drives': num_slow_drives,
            'total_drives': total_drives,
            'to_resp_ratio': to_resp_ratio
        })
        
        previous_score = new_score
        epoch_start = epoch_end
        epoch_count += 1
    
    print(f"✓ 检测完成! 处理了 {epoch_count} 个纪元")
    
    # 转换为 DataFrame
    scores_df = pd.DataFrame(score_db)
    
    # 识别 fail-slow 时段
    print("\n识别 fail-slow 时段...")
    score_threshold = 80
    min_duration_epochs = 3
    
    scores_df['is_high_score'] = scores_df['score'] > score_threshold
    scores_df['period_id'] = (
        scores_df['is_high_score'] != scores_df['is_high_score'].shift()
    ).cumsum()
    
    fail_slow_periods = []
    for period_id, group in scores_df[scores_df['is_high_score']].groupby('period_id'):
        if len(group) >= min_duration_epochs:
            fail_slow_periods.append({
                'start_time': group['timestamp'].min(),
                'end_time': group['timestamp'].max(),
                'max_score': group['score'].max(),
                'avg_score': group['score'].mean(),
                'duration_epochs': len(group)
            })
    
    print(f"✓ 发现 {len(fail_slow_periods)} 个 fail-slow 时段")
    
    # 打印摘要
    print("\n" + "="*60)
    print("检测摘要")
    print("="*60)
    print(f"总纪元数: {len(scores_df)}")
    print(f"平均健康分数: {scores_df['score'].mean():.2f}")
    print(f"最高健康分数: {scores_df['score'].max():.2f}")
    print(f"最低健康分数: {scores_df['score'].min():.2f}")
    print(f"分数标准差: {scores_df['score'].std():.2f}")
    print(f"\n平均中位延迟: {scores_df['median_latency'].mean():.2f} ms")
    print(f"最高中位延迟: {scores_df['median_latency'].max():.2f} ms")
    print(f"\nFail-Slow 时段数量: {len(fail_slow_periods)}")
    
    if len(fail_slow_periods) > 0:
        print("\nFail-Slow 时段详情:")
        for i, period in enumerate(fail_slow_periods, 1):
            print(f"\n  时段 {i}:")
            print(f"    开始: {period['start_time']}")
            print(f"    结束: {period['end_time']}")
            print(f"    最高分数: {period['max_score']:.2f}")
            print(f"    平均分数: {period['avg_score']:.2f}")
            print(f"    持续时长: {period['duration_epochs']} 个纪元 ({period['duration_epochs'] * EPOCH_SECONDS} 秒)")
        
        total_duration = sum(p['duration_epochs'] for p in fail_slow_periods)
        print(f"\n  总持续时间: {total_duration} 个纪元 ({total_duration * EPOCH_SECONDS} 秒)")
    
    print("="*60)
    
    # 导出结果
    output_file = 'result/iaso_scores.csv'
    scores_df.to_csv(output_file, index=False)
    print(f"\n✓ 结果已导出到: {output_file}")
    
    # 显示前10行结果
    print("\n前10行检测结果:")
    print(scores_df.head(10).to_string(index=False))
    
    print("\n✓ 所有操作完成!")
    
except FileNotFoundError:
    print("✗ 错误: 找不到 'a.csv' 文件")
except Exception as e:
    print(f"✗ 错误: {e}")
    import traceback
    traceback.print_exc()
