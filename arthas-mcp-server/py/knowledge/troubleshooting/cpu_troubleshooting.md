# CPU 高使用率排查手册

当应用 CPU 使用率异常升高时，按照以下步骤排查。

## 排查步骤

### Step 1：定位高 CPU 线程

使用 Arthas `thread` 命令找出 CPU 使用率最高的线程：

```bash
thread -n 3
```

该命令列出最忙的前 3 个线程及其堆栈，直接定位到热点代码。

### Step 2：分析线程堆栈

查看输出的堆栈信息，关注以下几点：

1. **热点方法**：堆栈顶部的方法通常是 CPU 消耗的主要来源
2. **循环调用**：检查是否存在无限循环或高频循环
3. **正则表达式**：复杂正则在特定输入下可能导致回溯爆炸
4. **序列化/反序列化**：大对象的频繁序列化可能消耗大量 CPU

### Step 3：使用 profiler 进行火焰图分析

如果 thread 命令无法直接定位，使用 Arthas 内置 profiler：

```bash
profiler start
# 等待一段时间（如 30 秒）
profiler stop --format html
```

生成火焰图后，可以直观看到 CPU 时间分布。

### Step 4：使用 watch 确认问题

定位到可疑方法后，使用 `watch` 命令观察方法的入参和耗时：

```bash
watch com.example.HotClass hotMethod "{params, returnObj}" '#cost>100' -x 2
```

## 常见原因

| 原因 | 特征 | 解决方案 |
|------|------|---------|
| 死循环 | 某线程 CPU 持续 100% | 修复循环条件 |
| 正则回溯 | 处理特定字符串时 CPU 飙高 | 优化正则表达式 |
| 频繁 GC | GC 线程占用大量 CPU | 调整 JVM 内存参数 |
| 线程争用 | 大量线程在 BLOCKED 状态 | 减少锁粒度 |
| 序列化 | 大对象频繁序列化 | 使用更高效的序列化方式 |

## 进阶排查

### GC 导致的 CPU 高

如果发现 GC 线程占用大量 CPU：

1. 使用 `jvm` 命令查看 GC 统计信息
2. 关注 COLLECTION-COUNT 和 COLLECTION-TIME
3. 如果 Full GC 频繁，可能存在内存泄漏

### 锁争用导致的 CPU 高

```bash
thread -b
```

使用 `-b` 参数找出阻塞其他线程的线程，分析死锁或锁争用问题。
