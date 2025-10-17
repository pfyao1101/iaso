#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IASO Fail-Slow Detection Framework Implementation
基于IASO论文的fail-slow检测脚本
"""

import os
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
import warnings
import argparse
warnings.filterwarnings('ignore')

# 设置中文字体支持
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial']
plt.rcParams['axes.unicode_minus'] = False

def parse_args():
    """
    解析命令行参数
    
    返回:
    - args: 解析后的命令行参数对象
    """
    parser = argparse.ArgumentParser(
        description='IASO Fail-Slow Detection Framework - 基于IASO论文的fail-slow检测脚本',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    # 数据文件参数
    parser.add_argument(
        '--input-file', '-i',
        type=str,
        default='data/a.csv',
        help='输入的CSV数据文件路径'
    )
    
    parser.add_argument(
        '--output-dir', '-o',
        type=str,
        default='result/',
        help='输出的评分CSV文件路径'
    )
    
    # IASO算法参数
    parser.add_argument(
        '--epoch-seconds', '-e',
        type=int,
        default=1,
        help='纪元时间窗口长度（秒）'
    )
    
    parser.add_argument(
        '--latency-multiplier', '-l',
        type=float,
        default=2.0,
        help='判定慢盘的延迟倍数阈值（相对于中位数延迟）'
    )
    
    parser.add_argument(
        '--min-ttr-minutes', '-t',
        type=float,
        default=2.0,
        help='从100分恢复到1分所需的最小时间（分钟）'
    )
    
    parser.add_argument(
        '--ratio-thresh', '-r',
        type=float,
        default=0.1,
        help='Timeout-Response比率的上限阈值, 控制分数增长'
    )
    
    # 检测参数
    parser.add_argument(
        '--hard-latency-threshold', '-a',
        type=float,
        default=100.0,
        help='慢盘的硬性延迟阈值（毫秒）'
    )
    
    parser.add_argument(
        '--score-threshold', '-s',
        type=float,
        default=80.0,
        help='Fail-slow分数阈值，超过此值视为fail-slow'
    )
    
    parser.add_argument(
        '--min-duration-epochs', '-d',
        type=int,
        default=3,
        help='Fail-slow最小持续纪元数'
    )
    
    # 输出控制参数
    parser.add_argument(
        '--no-plot',
        action='store_true',
        help='不生成可视化图表'
    )
    
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='输出详细信息'
    )
    
    return parser.parse_args()


class IasoDetector:
    """IASO Fail-Slow检测器"""
    
    def __init__(self, 
                 args):
        """
        初始化IASO检测器
        
        参数:
        - epoch_seconds: 纪元时间窗口（秒）
        - latency_multiplier: 判定慢盘的延迟倍数阈值
        - min_ttr_minutes: 恢复到满分所需的最小时间（分钟）
        - ratio_thresh: Timeout-Response比率的上限阈值
        """
        self.EPOCH_SECONDS = args.epoch_seconds
        self.LATENCY_MULTIPLIER = args.latency_multiplier
        self.MIN_TTR_MINUTES = args.min_ttr_minutes
        self.RATIO_THRESH = args.ratio_thresh
        self.HARD_LATENCY_THRESHOLD = args.hard_latency_threshold

        self.output_dir = args.output_dir
        os.makedirs(self.output_dir, exist_ok=True)

        self.data = None
        self.score_db = []
        self.fail_slow_periods = []
    
    def load_and_preprocess(self, file_path):
        """
        加载和预处理数据
        
        参数:
        - file_path: CSV文件路径
        """
        print(f"正在加载数据文件: {file_path}")
        
        # 加载CSV数据
        self.data = pd.read_csv(file_path)
        
        # 转换时间戳
        self.data['timestamp'] = pd.to_datetime(self.data['timestamp'], 
                                                format='%m/%d/%Y %I:%M:%S %p')
        
        # 按时间排序
        self.data = self.data.sort_values('timestamp').reset_index(drop=True)
        
        # 检查w_await列
        if 'w_await' not in self.data.columns:
            raise ValueError("数据中缺少 'w_await' 列")
        
        # 处理非数值或空值
        self.data['w_await'] = pd.to_numeric(self.data['w_await'], errors='coerce')
        self.data = self.data.dropna(subset=['w_await'])
        
        print(f"数据加载完成: {len(self.data)} 行")
        print(f"时间范围: {self.data['timestamp'].min()} 到 {self.data['timestamp'].max()}")
        print(f"设备列表: {self.data['device'].unique()}")

        # print(self.data)
        
    def detect_fail_slow(self):
        """
        执行fail-slow检测
        """
        if self.data is None:
            raise ValueError("请先加载数据!")
        
        print("\n开始执行IASO fail-slow检测...")
        
        # 获取时间范围
        start_time = self.data['timestamp'].min()
        end_time = self.data['timestamp'].max()
        
        # 初始化评分
        previous_score = 1.0
        epoch_start = start_time
        epoch_count = 0
        
        # 按纪元处理数据
        while epoch_start < end_time:
            epoch_end = epoch_start + timedelta(seconds=self.EPOCH_SECONDS)
            
            # 切片当前纪元的数据
            epoch_data = self.data[
                (self.data['timestamp'] >= epoch_start) & 
                (self.data['timestamp'] < epoch_end)
            ]
            
            if len(epoch_data) == 0:
                # 如果当前纪元没有数据，跳过
                epoch_start = epoch_end
                continue
            
            # 计算基准延迟（中位数）
            median_latency = epoch_data['w_await'].median()
            
            # 定义慢盘阈值
            relative_threshold = median_latency * self.LATENCY_MULTIPLIER
            latency_threshold = min(self.HARD_LATENCY_THRESHOLD, relative_threshold)

            # 统计慢盘数量
            num_slow_drives = (epoch_data[epoch_data['w_await'] > latency_threshold]['device']).nunique()
            
            # 统计总数据点数（作为总盘活跃度的近似）
            total_drives = epoch_data['device'].nunique()
            
            # 计算ToRespRatio
            if total_drives > 0:
                to_resp_ratio = num_slow_drives / total_drives
            else:
                to_resp_ratio = 0.0
            
            # 更新节点健康分数 (AIMD算法)
            if to_resp_ratio == 0:
                # 加性减少 (Additive Decrease)
                min_ttr_seconds = self.MIN_TTR_MINUTES * 60
                decrease_amount = (100 * self.EPOCH_SECONDS) / min_ttr_seconds
                new_score = previous_score - decrease_amount
                new_score = max(1.0, new_score)  # 确保分数不低于1
            else:
                # 乘性增加 (Multiplicative Increase)
                min_ratio = min(to_resp_ratio, self.RATIO_THRESH)
                near_thresh = min_ratio / self.RATIO_THRESH
                increase_amount = previous_score * near_thresh
                new_score = previous_score + increase_amount
                new_score = min(100.0, new_score)  # 确保分数不高于100
            
            # 存储分数
            self.score_db.append({
                'timestamp': epoch_end,
                'score': new_score,
                'median_latency': median_latency,
                'num_slow_drives': num_slow_drives,
                'num_total_drives': total_drives,
                'slow_drives': epoch_data[epoch_data['w_await'] > latency_threshold]['device'].unique().tolist(),
                'to_resp_ratio': to_resp_ratio
            })
            
            previous_score = new_score
            epoch_start = epoch_end
            epoch_count += 1
        
        print(f"检测完成! 共处理 {epoch_count} 个纪元")
    
    # TODO
    # 这个后续需要看数据是怎么组成的
    # 如果不同节点的数据在同一个文件里，那么需要先按节点分组再处理
    # 处理过程为计算每个节点在每个time interval的3-th分数，在进行聚类
    # 如果不在一个文件里，那么就读取不同节点的分数文件，再按上述方法进行处理
    def identify_fail_slow_periods(self, score_threshold=80, min_duration_epochs=3):
        """
        识别fail-slow时段
        
        参数:
        - score_threshold: 分数阈值，超过此值视为fail-slow
        - min_duration_epochs: 最小持续纪元数
        """
        if len(self.score_db) == 0:
            print("警告: 还没有执行检测!")
            return
        
        print(f"\n识别fail-slow时段 (阈值: {score_threshold}, 最小持续: {min_duration_epochs} 个纪元)...")
        
        scores_df = pd.DataFrame(self.score_db)
        
        scores_df['is_high_score'] = scores_df['score'] > score_threshold
        
        # 识别连续的高分时段
        scores_df['period_id'] = (
            scores_df['is_high_score'] != scores_df['is_high_score'].shift()
        ).cumsum()
        
        # 筛选出持续时间足够长的fail-slow时段
        self.fail_slow_periods = []
        for period_id, group in scores_df[scores_df['is_high_score']].groupby('period_id'):
            if len(group) >= min_duration_epochs:
                self.fail_slow_periods.append({
                    'start_time': group['timestamp'].min(),
                    'end_time': group['timestamp'].max(),
                    'max_score': group['score'].max(),
                    'avg_score': group['score'].mean(),
                    'duration_epochs': len(group)
                })
        
        print(f"发现 {len(self.fail_slow_periods)} 个fail-slow时段:")
        for i, period in enumerate(self.fail_slow_periods, 1):
            print(f"  {i}. {period['start_time']} 到 {period['end_time']}")
            print(f"     最高分数: {period['max_score']:.2f}, 平均分数: {period['avg_score']:.2f}, 持续: {period['duration_epochs']} 个纪元")
    
    def visualize(self):
        """
        可视化检测结果
        
        参数:
        - output_file: 输出图片文件名
        """
        # TODO
        # 按照主机名完成图片保存
        output_file = os.path.join(self.output_dir, 'host1.png')

        if len(self.score_db) == 0:
            print("警告: 还没有执行检测!")
            return
        
        print(f"\n生成可视化图表...")
        
        scores_df = pd.DataFrame(self.score_db)
        
        # 创建图表
        fig, axes = plt.subplots(3, 1, figsize=(15, 12))
        
        # 图1: 节点健康分数随时间变化
        ax1 = axes[0]
        ax1.plot(scores_df['timestamp'], scores_df['score'], 
                linewidth=2, color='#2E86AB', label='Health Score')
        ax1.axhline(y=80, color='red', linestyle='--', 
                   linewidth=1.5, label='Fail-Slow Threshold (80)')
        
        # 标记fail-slow时段
        for period in self.fail_slow_periods:
            ax1.axvspan(period['start_time'], period['end_time'], 
                       alpha=0.3, color='red')
        
        ax1.set_xlabel('Time', fontsize=12)
        ax1.set_ylabel('Health Score', fontsize=12)
        ax1.set_title('Node Health Score Over Time (IASO)', fontsize=14, fontweight='bold')
        ax1.legend(loc='best')
        ax1.grid(True, alpha=0.3)
        ax1.set_ylim([0, 105])
        
        # 图2: 中位延迟随时间变化
        ax2 = axes[1]
        ax2.plot(scores_df['timestamp'], scores_df['median_latency'], 
                linewidth=2, color='#A23B72', label='Median w_await')
        ax2.set_xlabel('Time', fontsize=12)
        ax2.set_ylabel('Median Latency (ms)', fontsize=12)
        ax2.set_title('Median Write Latency Over Time', fontsize=14, fontweight='bold')
        ax2.legend(loc='best')
        ax2.grid(True, alpha=0.3)
        
        # 图3: ToRespRatio随时间变化
        ax3 = axes[2]
        ax3.plot(scores_df['timestamp'], scores_df['to_resp_ratio'], 
                linewidth=2, color='#F18F01', label='Timeout-Response Ratio')
        ax3.axhline(y=self.RATIO_THRESH, color='orange', linestyle='--', 
                   linewidth=1.5, label=f'Ratio Threshold ({self.RATIO_THRESH})')
        ax3.set_xlabel('Time', fontsize=12)
        ax3.set_ylabel('ToRespRatio', fontsize=12)
        ax3.set_title('Timeout-Response Ratio Over Time', fontsize=14, fontweight='bold')
        ax3.legend(loc='best')
        ax3.grid(True, alpha=0.3)
        
        plt.tight_layout()
        plt.savefig(output_file, dpi=300, bbox_inches='tight')
        print(f"图表已保存到: {output_file}")
        plt.show()
    
    def export_results(self):
        """
        导出检测结果到CSV
        
        参数:
        - output_file: 输出CSV文件名
        """
        # TODO
        # 按照主机名完成文件保存
        output_file = os.path.join(self.output_dir, 'host1_scores.csv')

        if len(self.score_db) == 0:
            print("警告: 还没有执行检测!")
            return
        
        scores_df = pd.DataFrame(self.score_db)
        scores_df.to_csv(output_file, index=False)
        print(f"\n结果已导出到: {output_file}")
    
    # TODO
    def print_summary(self):
        """打印检测摘要"""
        if len(self.score_db) == 0:
            print("警告: 还没有执行检测!")
            return
        
        scores_df = pd.DataFrame(self.score_db)
        
        print("\n" + "="*60)
        print("IASO Fail-Slow Detection Summary")
        print("="*60)
        print(f"总纪元数: {len(scores_df)}")
        print(f"平均健康分数: {scores_df['score'].mean():.2f}")
        print(f"最高健康分数: {scores_df['score'].max():.2f}")
        print(f"最低健康分数: {scores_df['score'].min():.2f}")
        print(f"分数标准差: {scores_df['score'].std():.2f}")
        print(f"\n平均中位延迟: {scores_df['median_latency'].mean():.2f} ms")
        print(f"最高中位延迟: {scores_df['median_latency'].max():.2f} ms")
        print(f"\nFail-Slow时段数量: {len(self.fail_slow_periods)}")
        
        if len(self.fail_slow_periods) > 0:
            total_duration = sum(p['duration_epochs'] for p in self.fail_slow_periods)
            print(f"Fail-Slow总持续时间: {total_duration} 个纪元 "
                  f"({total_duration * self.EPOCH_SECONDS} 秒)")
        print("="*60)

if __name__ == "__main__":
    # 解析命令行参数
    args = parse_args()
    
    print("="*60)
    print("IASO Fail-Slow Detection Framework")
    print("="*60)
    
    if args.verbose:
        print("运行参数:")
        print(f"  输入文件: {args.input_file}")
        print(f"  输出CSV: {args.output_csv}")
        print(f"  输出图片: {args.output_plot}")
        print(f"  纪元时间窗口: {args.epoch_seconds} 秒")
        print(f"  延迟倍数阈值: {args.latency_multiplier}")
        print(f"  恢复时间: {args.min_ttr_minutes} 分钟")
        print(f"  比率阈值: {args.ratio_thresh}")
        print(f"  分数阈值: {args.score_threshold}")
        print(f"  最小持续纪元数: {args.min_duration_epochs}")
        print("="*60)
    
    # 创建检测器实例（使用命令行参数）
    detector = IasoDetector(
        args
    )
    
    # 加载数据
    detector.load_and_preprocess(args.input_file)
    
    # 执行检测
    detector.detect_fail_slow()
    
    # 导出结果
    detector.export_results()

    # 识别fail-slow时段
    detector.identify_fail_slow_periods(
        score_threshold=args.score_threshold, 
        min_duration_epochs=args.min_duration_epochs
    )
    
    # 打印摘要
    detector.print_summary()
    
    # 可视化
    if not args.no_plot:
        detector.visualize()
