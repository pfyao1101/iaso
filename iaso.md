# IASO
## **复现文档：使用IASO框架检测`host1.csv`中的Fail-Slow现象**

### **1. 目标**

我们的目标是基于 `IASO: A Fail-Slow Detection and Mitigation Framework` 论文中的核心思想，实现一个fail-slow检测脚本。最终输出**该主机（Node）在不同时间点的健康度分数**，并识别出潜在的fail-slow时段。

然后对依据计算所得的每个节点的分数实现慢节点的识别

### **2. 核心概念与数据映射**

在开始之前，我们必须将IASO的抽象概念与您的CSV数据列对应起来：

| IASO 概念 | 对应的数据/逻辑 | 说明 |
| :--- | :--- | :--- |
| **节点 (Node)** | 整个 `host1.csv` 文件所代表的主机 | IASO的评分对象是节点级别。 |
| **对等体 (Peer)** | CSV中的每个`device` (e.g., `nvme2n1`, `nvme3n1`) | 节点内的各个磁盘驱动器互为对等体。 |
| **纪元 (Epoch)** | 一个固定的时间窗口（epoch-seconds进行设定） | 我们将按时间窗口处理数据。 |
| **延迟 (Latency)** | CSV中的 `w_await` 列 (单位: ms) | 这是判断驱动器是否“慢”的核心指标。 |
| **超时信号 (Timeout)** | **逻辑模拟**：一个驱动器被判定为“慢” | 原始IASO依赖应用层超时。在这里，我们模拟它：如果一个盘的延迟远超其兄弟盘的两倍(latency-multiplier来进行设定)或者超过一个已经设定的阈值(hard-latency-threshold)，就记为一个“超时”。 |
| **健康分数 (Score)** | 一个介于1到100的浮点数 | 1代表完全健康，100代表严重fail-slow。 |
| **ScoreDB** | 一个数据结构 | 用于存储每个纪元计算出的节点分数。 |

### **3. 详细实现步骤**

#### **第零步：数据预处理**

1.  **加载数据**: 使用Pandas库加载 `host1.csv`。
2.  **处理时间戳**: 将 `timestamp` 列转换为标准的datetime对象，并将其设置为DataFrame的索引。这将极大地方便按时间窗口进行数据切片。
3.  **数据清洗**: 检查`w_await`等关键列是否存在非数值或空值，并进行相应处理（如填充或删除）。

#### **第一步：定义框架参数**

在代码的开头，将所有可调参数清晰地定义出来。这些参数直接源于IASO论文，是算法的核心。

#### **第二步：主循环 - 按纪元处理数据**

这是算法的核心。你需要编写一个循环，按 `EPOCH_SECONDS` 的步长遍历整个数据集。

**对于循环中的每一个纪元 (Epoch):**

1.  **切片数据**: 获取当前时间窗口内（例如 `t` 到 `t + 15s`）的所有数据行。

2.  **模拟“超时信号” (关键步骤)**:
    *   **计算慢盘阈值**。
    *   **统计慢盘数量**: 遍历当前纪元内的所有数据点，统计有多少个驱动器的`w_await`值**超过**了`latency_threshold`。得到 `num_slow_drives`。
    *   **统计总盘数**: 获取当前节点上活跃的驱动器总数 `total_drives`。

3.  **计算`ToRespRatio`**:
    *   这个比率是IASO评分算法的直接输入。
    *   `ToRespRatio = num_slow_drives / total_drives`

4.  **更新节点健康分数 (AIMD算法)**:
    *   从一个变量 `previous_score` 中获取上一纪元的分数（初始值设为1）。
    *   **情况A：无慢盘 (加性减少 - Additive Decrease)**
        *   **IF `ToRespRatio == 0`**:
            *   `min_ttr_seconds = MIN_TTR_MINUTES * 60`
            *   `decrease_amount = (100 * EPOCH_SECONDS) / min_ttr_seconds`
            *   `new_score = previous_score - decrease_amount`
            *   **确保分数不低于1**: `new_score = max(1.0, new_score)`

    *   **情况B：有慢盘 (乘性增加 - Multiplicative Increase)**
        *   **ELSE**:
            *   `minRatio = min(ToRespRatio, RATIO_THRESH)`
            *   `nearThresh = minRatio / RATIO_THRESH`
            *   `increase_amount = previous_score * nearThresh`
            *   `new_score = previous_score + increase_amount`
            *   **确保分数不高于100**: `new_score = min(100.0, new_score)`
            TODO
            节点中的盘的个数太少导致分数增加地过快

5.  **存储分数**:
    *   将当前纪元的结束时间戳和计算出的 `new_score` 存入你的“ScoreDB”（例如，一个列表或字典）。
    *   更新 `previous_score = new_score`，为下一个纪元做准备。

#### **第三步：结果分析与可视化**

当循环结束后，你的"ScoreDB"中就存储了该主机随时间变化的完整健康度分数。

1.  **识别Fail-Slow时段**:
    *   原始IASO会使用DBSCAN比较多个节点的分数。由于我们只有一个节点，最直接的方法是**设定一个分数阈值**。
    *   例如，你可以认为当节点分数**持续高于80**超过一定时间（比如连续3个纪元），就标记该时段为潜在的fail-slow期。

2.  **可视化**:
    *   使用`matplotlib`或`seaborn`库，绘制节点健康分数的时序图。
    *   在图上可以清晰地看到分数何时平稳（在低位），何时开始攀升，何时达到峰值，以及何时恢复。
    *   可以将识别出的fail-slow时段在图上用不同颜色或垂直线标记出来，使其一目了然。


#### **第四步 慢节点的识别**

在一段设定的time interval（IASO文中设定为10min）中计算每个节点的30 th value然后使用DBSCAN进行聚类