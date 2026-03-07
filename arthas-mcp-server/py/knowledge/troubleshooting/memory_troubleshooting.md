# 内存问题排查手册

当应用出现内存相关问题（OOM、内存泄漏、频繁 GC）时，按照以下步骤排查。

## 排查步骤

### Step 1：查看 JVM 内存概况

使用 Arthas `jvm` 命令查看当前内存使用情况：

```bash
jvm
```

重点关注 MEMORY 部分的 HEAP-MEMORY-USAGE：
- **init**: 初始堆大小
- **used**: 已使用的堆大小
- **committed**: 已提交的堆大小
- **max**: 最大堆大小

如果 used 接近 max，说明堆内存即将耗尽。

### Step 2：分析 GC 情况

在 `jvm` 命令的 GARBAGE-COLLECTORS 部分查看：
- COLLECTION-COUNT：GC 次数
- COLLECTION-TIME：GC 总耗时

如果 Full GC 频繁（COLLECTION-COUNT 快速增长），大概率存在内存泄漏。

### Step 3：使用 heapdump 导出堆快照

```bash
heapdump /tmp/dump.hprof
```

**注意**：heapdump 是高危操作，会导致 JVM STW（Stop The World），建议在非高峰期执行。导出后使用 MAT（Eclipse Memory Analyzer）或 JProfiler 分析。

### Step 4：使用 vmtool 在线分析

不想 heapdump 时，可以使用 vmtool 命令查看对象实例：

```bash
vmtool --action getInstances --className java.util.HashMap --limit 10
```

## 常见内存问题

| 问题 | 特征 | 排查方法 |
|------|------|---------|
| 堆内存溢出 | java.lang.OutOfMemoryError: Java heap space | heapdump 分析大对象 |
| 元空间溢出 | OutOfMemoryError: Metaspace | 检查动态类加载/生成 |
| 内存泄漏 | used 持续增长不回落 | heapdump 对比分析 |
| GC 压力大 | Full GC 频繁，应用卡顿 | 调优 GC 参数或增大堆 |

## 进阶排查

### 对象保留分析

通过 watch 命令观察可疑对象的生命周期：

```bash
watch com.example.CacheManager put "{params}" -x 3
```

观察缓存是否无限增长、是否有对象未被正确释放。
