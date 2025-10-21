

# IASO Fail-Slow Detection Framework

基于 IASO 论文实现的 fail-slow 检测框架，用于分析磁盘 I/O 性能数据并识别慢盘现象。

## 功能特性

- ✅ 按时间窗口（Epoch）处理数据
- ✅ 基于中位数延迟的慢盘检测
- ✅ AIMD（加性增加、乘性减少）评分算法
- ✅ Fail-slow 节点识别
- ✅ 详细的可视化图表
- ✅ 结果导出（CSV 格式）


## 参数调优指南

### EPOCH_SECONDS（纪元时间窗口）

- **默认值**: 15 秒
- **说明**: 较小的值能更快响应变化但可能引入噪声；较大的值更平滑但响应慢
- **推荐**: 根据数据采样频率调整，通常 10-30 秒

### LATENCY_MULTIPLIER（延迟倍数阈值）

- **默认值**: 2.0
- **说明**: **最关键的灵敏度参数**
  - 值越低（如 1.5）：检测器越敏感，但可能产生更多误报
  - 值越高（如 3.0）：检测器越不敏感，只报告严重的延迟问题
- **推荐**: 从 2.0 开始，根据实际情况调整

### MIN_TTR_MINUTES（恢复时间）

- **默认值**: 2.0 分钟
- **说明**: 控制分数恢复速度
- **推荐**: 系统负载变化快时可适当调低

### RATIO_THRESH（比率阈值）

- **默认值**: 0.1
- **说明**: 控制分数增长的"加速度"
- **推荐**: 希望系统快速提高警报级别时可降低此值

## 核心算法

### AIMD 评分算法

```
IF ToRespRatio == 0:
    # 加性减少
    new_score = previous_score - (100 * EPOCH_SECONDS) / (MIN_TTR_MINUTES * 60)
    new_score = max(1.0, new_score)
ELSE:
    # 乘性增加
    minRatio = min(ToRespRatio, RATIO_THRESH)
    nearThresh = minRatio / RATIO_THRESH
    new_score = previous_score + (previous_score * nearThresh)
    new_score = min(100.0, new_score)
```

### 慢盘检测逻辑

1. 计算当前纪元所有设备的 `w_await` 中位数
2. 慢盘阈值 = 中位数 × LATENCY_MULTIPLIER
3. 统计超过阈值的设备数量
4. 计算 ToRespRatio = 慢盘数量 / 总设备数

## 数据格式要求

CSV 文件必须包含以下列：

- `timestamp`: 时间戳（格式：MM/DD/YYYY HH:MM:SS AM/PM）
- `device`: 设备名称
- `w_await`: 写入等待时间（毫秒）

示例：
```csv
timestamp,device,w_await
09/17/2025 04:42:51 PM,nvme2n1,238.0
09/17/2025 04:42:51 PM,nvme3n1,249.87
```

## 许可证

MIT License

## 参考文献

- IASO: A Fail-Slow Detection and Mitigation Framework
- Perseus: A Fail-Slow Detection Framework for Cloud Storage Systems
