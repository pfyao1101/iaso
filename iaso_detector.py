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
import yaml
from sklearn.cluster import DBSCAN
warnings.filterwarnings('ignore')

# 设置中文字体支持
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial']
plt.rcParams['axes.unicode_minus'] = False


class Config:
    """配置类，用于存储从config.yaml文件读取的配置参数"""
    
    def __init__(self, config_file='config.yaml'):
        """从配置文件初始化配置"""
        try:
            with open(config_file, 'r', encoding='utf-8') as file:
                config_data = yaml.safe_load(file)
        except FileNotFoundError:
            print(f"警告: 配置文件 {config_file} 未找到，使用默认配置")
            config_data = {}
        
        # 数据文件参数
        self.input_files = config_data.get('input_files', ['data/host1-iostat.csv'])
        self.output_dir = config_data.get('output_dir', 'result/')
        
        # IASO算法参数
        self.epoch_seconds = config_data.get('epoch_seconds', 15)
        self.latency_multiplier = config_data.get('latency_multiplier', 2.0)
        self.min_ttr_minutes = config_data.get('min_ttr_minutes', 2.0)
        self.ratio_thresh = config_data.get('ratio_thresh', 0.1)
        
        # 检测参数
        self.hard_latency_threshold = config_data.get('hard_latency_threshold', 100.0)
        # self.score_threshold = config_data.get('score_threshold', 20.0)
        
        # 慢节点识别参数
        self.time_interval_minutes = config_data.get('time_interval_minutes', 10)
        self.dbscan_eps = config_data.get('dbscan_eps', 5.0)
        self.dbscan_min_samples = config_data.get('dbscan_min_samples', 2)
        
        # 输出控制参数
        self.no_plot = config_data.get('no_plot', False)
        self.verbose = config_data.get('verbose', False)


def load_config(config_file='config.yaml'):
    """
    从配置文件加载配置参数
    
    参数:
    - config_file: 配置文件路径
    
    返回:
    - config: 配置对象
    """
    return Config(config_file)


class IasoDetector:
    """IASO Fail-Slow检测器"""
    
    def __init__(self, 
                 config):
        """
        初始化IASO检测器
        
        参数:
        - config: 配置对象，包含所有必要参数
        """
        self.EPOCH_SECONDS = config.epoch_seconds
        self.LATENCY_MULTIPLIER = config.latency_multiplier
        self.MIN_TTR_MINUTES = config.min_ttr_minutes
        self.RATIO_THRESH = config.ratio_thresh
        self.HARD_LATENCY_THRESHOLD = config.hard_latency_threshold

        self.output_dir = config.output_dir
        os.makedirs(self.output_dir, exist_ok=True)

        # 多节点数据结构
        self.nodes_data = {}  # 存储每个节点的数据
        self.nodes_score_db = {}  # 存储每个节点的分数数据库
        self.slow_nodes = []  # 存储识别出的慢节点
        
        # 慢节点识别参数
        self.time_interval_minutes = config.time_interval_minutes
        self.dbscan_eps = config.dbscan_eps
        self.dbscan_min_samples = config.dbscan_min_samples
    
    def load_and_preprocess_multiple(self, file_paths):
        """
        加载和预处理多个节点的数据
        
        参数:
        - file_paths: CSV文件路径列表
        """
        print(f"正在加载 {len(file_paths)} 个节点的数据文件...")
        
        for file_path in file_paths:
            # 从文件名提取节点名
            node_name = os.path.basename(file_path).split('-')[0]  # e.g., "host1" from "host1-iostat.csv"
            
            print(f"  加载节点 {node_name}: {file_path}")
            
            try:
                # 加载CSV数据
                data = pd.read_csv(file_path)
                
                # 转换时间戳
                data['timestamp'] = pd.to_datetime(data['timestamp'], 
                                                   format='%m/%d/%Y %I:%M:%S %p')
                
                # 按时间排序
                data = data.sort_values('timestamp').reset_index(drop=True)
                
                # 检查w_await列
                if 'w_await' not in data.columns:
                    print(f"    警告: 节点 {node_name} 数据中缺少 'w_await' 列，跳过")
                    continue
                
                # 处理非数值或空值
                data['w_await'] = pd.to_numeric(data['w_await'], errors='coerce')
                data = data.dropna(subset=['w_await'])
                
                # 存储到节点数据字典
                self.nodes_data[node_name] = data
                self.nodes_score_db[node_name] = []
                
                print(f"    ✓ 节点 {node_name} 加载完成: {len(data)} 行")
                print(f"    ✓ 时间范围: {data['timestamp'].min()} 到 {data['timestamp'].max()}")
                print(f"    ✓ 设备列表: {data['device'].unique()}")
                
            except Exception as e:
                print(f"    ✗ 节点 {node_name} 加载失败: {e}")
        
        print(f"\n成功加载 {len(self.nodes_data)} 个节点的数据")
        
    def detect_fail_slow(self):
        """
        执行多节点fail-slow检测
        """
        if not self.nodes_data:
            raise ValueError("请先加载数据!")
        
        print(f"\n开始执行IASO fail-slow检测 ({len(self.nodes_data)} 个节点)...")
        
        for node_name, data in self.nodes_data.items():
            print(f"\n处理节点: {node_name}")
            self._detect_single_node(node_name, data)
    
    def _detect_single_node(self, node_name, data):
        """
        执行单个节点的fail-slow分数计算
        
        参数:
        - node_name: 节点名称
        - data: 节点数据
        """
        # 获取时间范围
        start_time = data['timestamp'].min()
        end_time = data['timestamp'].max()
        
        # 初始化评分
        previous_score = 1.0
        epoch_start = start_time
        epoch_count = 0
        
        # 按纪元处理数据
        while epoch_start < end_time:
            epoch_end = epoch_start + timedelta(seconds=self.EPOCH_SECONDS)
            
            # 切片当前纪元的数据
            epoch_data = data[
                (data['timestamp'] >= epoch_start) & 
                (data['timestamp'] < epoch_end)
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
            self.nodes_score_db[node_name].append({
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
        
        print(f"  节点 {node_name} 检测完成! 共处理 {epoch_count} 个epoch")
    
    # TODO
    # 需要检查当前函数和他所调用的函数的逻辑是否正确
    def identify_slow_nodes(self):
        """
        识别慢节点 - 基于IASO论文中的方法
        在指定时间间隔内计算每个节点的30th百分位分数，然后使用DBSCAN进行聚类
        """
        if not self.nodes_score_db:
            print("警告: 还没有执行检测!")
            return
        
        print(f"\n开始识别慢节点 (时间间隔: {self.time_interval_minutes} 分钟)...")
        
        # 获取所有节点的时间范围
        all_timestamps = []
        for node_name, score_db in self.nodes_score_db.items():
            if score_db:
                timestamps = [entry['timestamp'] for entry in score_db]
                all_timestamps.extend(timestamps)
        
        if not all_timestamps:
            print("警告: 没有分数数据可用于慢节点识别!")
            return

        # print(all_timestamps)

        # 确定时间范围
        start_time = min(all_timestamps)
        end_time = max(all_timestamps)
        
        # 按时间间隔处理
        interval_data = []
        current_time = start_time
        interval_seconds = self.time_interval_minutes * 60
        
        while current_time < end_time:
            interval_end = current_time + timedelta(seconds=interval_seconds)
            
            # 收集当前时间间隔内每个节点的分数
            interval_scores = {}
            for node_name, score_db in self.nodes_score_db.items():
                scores_in_interval = []
                for entry in score_db:
                    if current_time <= entry['timestamp'] < interval_end:
                        scores_in_interval.append(entry['score'])
                
                if scores_in_interval:
                    # 计算30th百分位数
                    percentile_30th = np.percentile(scores_in_interval, 30)
                    interval_scores[node_name] = percentile_30th
            
            if interval_scores:
                interval_data.append({
                    'timestamp': interval_end,
                    'scores': interval_scores
                })
            
            current_time = interval_end
        
        if not interval_data:
            print("警告: 没有足够的数据进行慢节点识别!")
            return

        # print(interval_data)

        # # 以csv导出每个时间窗口的节点的30-th分数
        # interval_output_file = os.path.join(self.output_dir, 'slow_node_interval_scores.csv')
        # interval_rows = []
        # for interval in interval_data:
        #     row = {'timestamp': interval['timestamp']}
        #     row.update(interval['scores'])
        #     interval_rows.append(row)
        # interval_df = pd.DataFrame(interval_rows)
        # interval_df.to_csv(interval_output_file, index=False)
        # print(f"  时间间隔节点30th分数已导出到: {interval_output_file}")

        print(f"收集到 {len(interval_data)} 个时间间隔的数据")
        
        # 进行DBSCAN聚类识别慢节点
        self._cluster_nodes_with_dbscan(interval_data)
    
    # TODO
    def _cluster_nodes_with_dbscan(self, interval_data):
        """
        使用DBSCAN对每个时间间隔的节点分数进行聚类，限定结果为两类
        
        参数:
        - interval_data: 包含每个时间间隔节点分数的数据
        """
        if len(interval_data) == 0:
            print("警告: 没有时间间隔数据，无法进行聚类分析!")
            return
            
        # 获取所有节点名称
        all_nodes = set()
        for data in interval_data:
            all_nodes.update(data['scores'].keys())
        all_nodes = sorted(list(all_nodes))
        
        if len(all_nodes) < 2:
            print("警告: 节点数量不足，无法进行聚类分析!")
            return
            
        print(f"对 {len(interval_data)} 个时间间隔进行DBSCAN聚类，节点: {all_nodes}")
        
        # 记录每个时间间隔的聚类结果
        interval_clustering_results = []
        
        for i, data in enumerate(interval_data):
            timestamp = data['timestamp']
            scores = data['scores']
            
            node_scores = []
            node_names = []
            for node in all_nodes:
                if node in scores:
                    node_scores.append(scores[node])
                    node_names.append(node)
            
            if len(node_scores) < 2:
                continue
                
            # 准备DBSCAN输入数据 (每个节点的分数作为一个样本点)
            X = np.array([[score] for score in node_scores])
            
            # 尝试不同的eps值来得到最多两类的聚类结果
            best_clustering = None
            best_eps = None
            best_n_clusters = 0
            
            # 从配置文件获取DBSCAN参数
            eps_values = [self.dbscan_eps, self.dbscan_eps * 0.5, 
                         self.dbscan_eps * 2.0, self.dbscan_eps * 0.25,
                         self.dbscan_eps * 4.0]
            
            for eps in eps_values:
                dbscan = DBSCAN(eps=eps, min_samples=self.dbscan_min_samples)
                labels = dbscan.fit_predict(X)
                
                # 计算聚类数量（排除噪声点 -1）
                unique_labels = set(labels)
                n_clusters = len(unique_labels) - (1 if -1 in unique_labels else 0)
                
                # 选择聚类数量最多为2且最接近2的结果
                if n_clusters <= 2 and n_clusters > best_n_clusters:
                    best_clustering = labels
                    best_eps = eps
                    best_n_clusters = n_clusters
            
            # 如果DBSCAN无法得到有效聚类（最多2类），使用默认参数
            if best_clustering is None:
                print(f"  时间间隔 {i+1} ({timestamp}): DBSCAN无法得到有效聚类，使用默认参数")
                dbscan = DBSCAN(eps=self.dbscan_eps, min_samples=self.dbscan_min_samples)
                best_clustering = dbscan.fit_predict(X)
                best_eps = self.dbscan_eps
                
                # 检查聚类数量
                unique_labels = set(best_clustering)
                best_n_clusters = len(unique_labels) - (1 if -1 in unique_labels else 0)
            
            # 记录此时间间隔的聚类结果
            interval_result = {
                'timestamp': timestamp,
                'eps': best_eps,
                'node_clustering': {}
            }
            
            for j, node in enumerate(node_names):
                cluster_id = int(best_clustering[j])
                interval_result['node_clustering'][node] = {
                    'score': node_scores[j],
                    'cluster': cluster_id
                }
            
            interval_clustering_results.append(interval_result)

            # print(interval_result)
            
            # 输出此时间间隔的聚类结果
            clusters = {}
            for node, info in interval_result['node_clustering'].items():
                cluster_id = info['cluster']
                if cluster_id not in clusters:
                    clusters[cluster_id] = []
                clusters[cluster_id].append(node)
            
            print(f"  时间间隔 {i+1} ({timestamp}), eps={best_eps}, 聚类数={best_n_clusters}:")
            for cluster_id in sorted(clusters.keys()):
                if cluster_id == -1:
                    print(f"    噪声点: {clusters[cluster_id]}")
                else:
                    print(f"    类别{cluster_id}: {clusters[cluster_id]}")
        
        # 统计每个节点在所有时间间隔中被分到类别1的次数（假设类别1是"慢节点"类别）
        node_slow_counts = {}
        total_intervals = len(interval_clustering_results)
        
        for node in all_nodes:
            slow_count = 0
            for result in interval_clustering_results:
                if node in result['node_clustering']:
                    # 判断哪个类别的平均分数更高，将其视为"慢节点"类别
                    cluster_scores = {}
                    for n, info in result['node_clustering'].items(): # info: {'score': xx, 'cluster': yy}
                        cluster_id = info['cluster']
                        # 排除噪声点
                        if cluster_id != -1:
                            if cluster_id not in cluster_scores:
                                cluster_scores[cluster_id] = []
                            cluster_scores[cluster_id].append(info['score'])
                    
                    # 如果只有噪声点或没有有效聚类，跳过此时间间隔
                    if not cluster_scores:
                        continue
                    
                    # 计算每个类别的平均分数
                    cluster_avg_scores = {}
                    for cluster_id, scores in cluster_scores.items():
                        cluster_avg_scores[cluster_id] = np.mean(scores)
                    
                    # 找到平均分数最高的类别作为"慢节点"类别
                    slow_cluster = max(cluster_avg_scores.keys(), key=lambda x: cluster_avg_scores[x])
                    
                    # 只有当节点不是噪声点且属于慢节点类别时才计数
                    node_cluster = result['node_clustering'][node]['cluster']
                    if node_cluster != -1 and node_cluster == slow_cluster:
                        slow_count += 1
            
            node_slow_counts[node] = slow_count
        # 有可能聚类结果只有一类，那么需要再进行判断

        # 设置结果
        # 节点实在太少了
        # 如果节点多的话这一步应该是不需要的
        self.slow_nodes = []
        for node in all_nodes:
            slow_ratio = node_slow_counts[node] / total_intervals if total_intervals > 0 else 0
            is_slow = slow_ratio > 0.5  # 超过一半的时间间隔被分类为慢节点
            
            # 计算该节点的平均分数
            avg_score = 0
            count = 0
            for result in interval_clustering_results:
                if node in result['node_clustering']:
                    avg_score += result['node_clustering'][node]['score']
                    count += 1
            avg_score = avg_score / count if count > 0 else 0
            
            self.slow_nodes.append({
                'node_name': node,
                'avg_score': avg_score,
                'slow_ratio': slow_ratio,
                'slow_count': node_slow_counts[node],
                'is_slow': is_slow
            })
        
        # 输出最终结果
        slow_count = sum(1 for node in self.slow_nodes if node['is_slow'])
        print(f"\n最终慢节点识别结果 (基于 {total_intervals} 个时间间隔的DBSCAN聚类):")
        print(f"  总节点数: {len(self.slow_nodes)}")
        print(f"  慢节点数: {slow_count}")
        
        print(f"  节点详细信息:")
        for node in self.slow_nodes:
            status = "慢节点" if node['is_slow'] else "正常节点"
            print(f"    - {node['node_name']}: 平均分数 {node['avg_score']:.2f}, "
                  f"慢节点比例 {node['slow_ratio']:.2f} ({node['slow_count']}/{total_intervals}), "
                  f"状态: {status}")
        
        # 保存聚类结果到文件
        clustering_output_file = os.path.join(self.output_dir, 'dbscan_clustering_results.csv')
        clustering_rows = []
        for result in interval_clustering_results:
            for node, info in result['node_clustering'].items():
                clustering_rows.append({
                    'timestamp': result['timestamp'],
                    'eps': result['eps'],
                    'node': node,
                    'score': info['score'],
                    'cluster': info['cluster'],
                    "slow_count": node_slow_counts[node],
                })
        
        if clustering_rows:
            clustering_df = pd.DataFrame(clustering_rows)
            clustering_df.to_csv(clustering_output_file, index=False)
            print(f"  DBSCAN聚类详细结果已导出到: {clustering_output_file}")    
    
    def visualize(self):
        """
        可视化多节点检测结果
        """
        if not self.nodes_score_db:
            print("警告: 还没有执行检测!")
            return
        
        print(f"\n生成可视化图表...")
        
        # 为每个节点生成单独的图表
        for node_name, score_db in self.nodes_score_db.items():
            if not score_db:
                continue
            
            self._visualize_single_node(node_name, score_db)
        
        # 生成综合比较图表
        self._visualize_comparison()
    
    def _visualize_single_node(self, node_name, score_db):
        """
        为单个节点生成可视化图表
        """
        output_file = os.path.join(self.output_dir, f'{node_name}.png')
        
        scores_df = pd.DataFrame(score_db)
        
        # 创建图表
        fig, axes = plt.subplots(3, 1, figsize=(15, 12))
        
        # 图1: 节点健康分数随时间变化
        ax1 = axes[0]
        ax1.plot(scores_df['timestamp'], scores_df['score'], 
                linewidth=2, color='#2E86AB', label='Health Score')
        ax1.axhline(y=80, color='red', linestyle='--', 
                   linewidth=1.5, label='Fail-Slow Threshold (80)')
        
        ax1.set_xlabel('Time', fontsize=12)
        ax1.set_ylabel('Score', fontsize=12)
        ax1.set_title(f'{node_name} Score Over Time (IASO)', fontsize=14, fontweight='bold')
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
        ax3.set_title('ToRespRatio', fontsize=14, fontweight='bold')
        ax3.legend(loc='best')
        ax3.grid(True, alpha=0.3)
        
        plt.tight_layout()
        plt.savefig(output_file, dpi=300, bbox_inches='tight')
        print(f"  {node_name} 图表已保存到: {output_file}")
        plt.close()  # 关闭图表以释放内存
    
    def _visualize_comparison(self):
        """
        生成多节点比较图表
        """
        output_file = os.path.join(self.output_dir, 'nodes_comparison.png')
        
        if len(self.nodes_score_db) < 2:
            print("  节点数量不足，跳过比较图表生成")
            return
        
        # 创建比较图表
        fig, axes = plt.subplots(2, 1, figsize=(15, 10))
        
        # 图1: 所有节点的健康分数对比
        ax1 = axes[0]
        colors = ['#2E86AB', '#A23B72', '#F18F01', '#C73E1D', '#591E71']
        
        for i, (node_name, score_db) in enumerate(self.nodes_score_db.items()):
            if not score_db:
                continue
            
            scores_df = pd.DataFrame(score_db)
            color = colors[i % len(colors)]
            
            # 标记慢节点
            is_slow = any(node['is_slow'] and node['node_name'] == node_name 
                         for node in self.slow_nodes) if self.slow_nodes else False
            linestyle = '--' if is_slow else '-'
            linewidth = 3 if is_slow else 2
            
            ax1.plot(scores_df['timestamp'], scores_df['score'], 
                    color=color, linestyle=linestyle, linewidth=linewidth, 
                    label=f"{node_name}{'(慢节点)' if is_slow else ''}")
        
        ax1.axhline(y=80, color='red', linestyle=':', 
                   linewidth=1.5, label='Fail-Slow Threshold')
        ax1.set_xlabel('Time', fontsize=12)
        ax1.set_ylabel('Score', fontsize=12)
        ax1.set_title('Multi-Node Score Comparison', fontsize=14, fontweight='bold')
        ax1.legend(loc='best')
        ax1.grid(True, alpha=0.3)
        ax1.set_ylim([0, 105])
        
        # 图2: 节点平均分数对比柱状图
        ax2 = axes[1]
        if self.slow_nodes:
            node_names = [node['node_name'] for node in self.slow_nodes]
            avg_scores = [node['avg_score'] for node in self.slow_nodes]
            colors_bar = ['red' if node['is_slow'] else 'lightblue' for node in self.slow_nodes]
            
            bars = ax2.bar(node_names, avg_scores, color=colors_bar)
            ax2.set_xlabel('Node', fontsize=12)
            ax2.set_ylabel('Average Score', fontsize=12)
            ax2.set_title('Average Score by Node', fontsize=14, fontweight='bold')
            ax2.grid(True, alpha=0.3, axis='y')
            
            # 添加数值标签
            for bar, score in zip(bars, avg_scores):
                height = bar.get_height()
                ax2.text(bar.get_x() + bar.get_width()/2., height + 0.5,
                        f'{score:.1f}', ha='center', va='bottom')
        
        plt.tight_layout()
        plt.savefig(output_file, dpi=300, bbox_inches='tight')
        print(f"  比较图表已保存到: {output_file}")
        plt.close()
    
    def export_results(self):
        """
        导出多节点检测结果到CSV
        """
        if not self.nodes_score_db:
            print("警告: 还没有执行检测!")
            return
        
        print(f"\n导出检测结果...")
        
        # 为每个节点导出分数数据
        for node_name, score_db in self.nodes_score_db.items():
            if not score_db:
                continue
            
            output_file = os.path.join(self.output_dir, f'{node_name}_scores.csv')
            scores_df = pd.DataFrame(score_db)
            scores_df.to_csv(output_file, index=False)
            print(f"  {node_name} 结果已导出到: {output_file}")
    
        
        # 导出综合摘要
        summary_file = os.path.join(self.output_dir, 'detection_summary.csv')
        summary_data = []
        
        for node_name, score_db in self.nodes_score_db.items():
            if not score_db:
                continue
            
            scores_df = pd.DataFrame(score_db)
            
            # 查找该节点是否为慢节点
            is_slow = any(node['is_slow'] and node['node_name'] == node_name 
                         for node in self.slow_nodes) if self.slow_nodes else False
            avg_slow_score = next((node['avg_score'] for node in self.slow_nodes 
                                  if node['node_name'] == node_name), 0)
            
            summary_data.append({
                'node_name': node_name,
                'total_epochs': len(score_db),
                'avg_score': scores_df['score'].mean(),
                'max_score': scores_df['score'].max(),
                'min_score': scores_df['score'].min(),
                'std_score': scores_df['score'].std(),
                'is_slow_node': is_slow,
                # 'slow_node_score': avg_slow_score
            })
        
        summary_df = pd.DataFrame(summary_data)
        summary_df.to_csv(summary_file, index=False)
        print(f"  检测摘要已导出到: {summary_file}")
    
    def print_summary(self):
        """打印多节点检测摘要"""
        if not self.nodes_score_db:
            print("警告: 还没有执行检测!")
            return
        
        print("\n" + "="*80)
        print("IASO Multi-Node Fail-Slow Detection Summary")
        print("="*80)
        
        total_epochs = 0
        
        for node_name, score_db in self.nodes_score_db.items():
            if not score_db:
                continue
            
            scores_df = pd.DataFrame(score_db)
            
            print(f"\n节点: {node_name}")
            print(f"  总纪元数: {len(scores_df)}")
            print(f"  平均分数: {scores_df['score'].mean():.2f}")
            print(f"  最高分数: {scores_df['score'].max():.2f}")
            print(f"  最低分数: {scores_df['score'].min():.2f}")
            print(f"  分数标准差: {scores_df['score'].std():.2f}")
            print(f"  平均中位延迟: {scores_df['median_latency'].mean():.2f} ms")
            print(f"  最高中位延迟: {scores_df['median_latency'].max():.2f} ms")
            
            total_epochs += len(scores_df)
        
        print(f"\n总体统计:")
        print(f"  节点总数: {len(self.nodes_score_db)}")
        print(f"  总epoch数: {total_epochs}")
        
        if self.slow_nodes:
            slow_count = sum(1 for node in self.slow_nodes if node['is_slow'])
            print(f"\n慢节点识别结果:")
            print(f"  慢节点数量: {slow_count} / {len(self.slow_nodes)}")
            
            if slow_count > 0:
                print(f"  慢节点列表:")
                for node in self.slow_nodes:
                    if node['is_slow']:
                        print(f"    - {node['node_name']}: 平均分数 {node['avg_score']:.2f}")
        
        print("="*80)

if __name__ == "__main__":
    # 从配置文件加载配置
    config = load_config()
    
    print("="*60)
    print("IASO Fail-Slow Detection Framework")
    print("="*60)
    
    if config.verbose:
        print("运行参数:")
        print(f"  输入文件: {config.input_files}")
        print(f"  输出目录: {config.output_dir}")
        print(f"  epoch时间: {config.epoch_seconds} 秒")
        print(f"  延迟倍数阈值: {config.latency_multiplier}")
        print(f"  恢复时间: {config.min_ttr_minutes} 分钟")
        print(f"  比率阈值: {config.ratio_thresh}")
        print(f"  硬性延迟阈值: {config.hard_latency_threshold} 毫秒")
        # print(f"  分数阈值: {config.score_threshold}")
        print(f"  慢节点识别时间间隔: {config.time_interval_minutes} 分钟")
        print("="*60)
    
    # 创建检测器实例（使用配置参数）
    detector = IasoDetector(
        config
    )
    
    # 加载多节点数据
    detector.load_and_preprocess_multiple(config.input_files)
    
    # 执行检测
    detector.detect_fail_slow()
    
    # 识别慢节点
    detector.identify_slow_nodes()
    
    # 导出结果
    detector.export_results()
    
    # 打印摘要
    detector.print_summary()
    
    # 可视化
    if not config.no_plot:
        detector.visualize()
