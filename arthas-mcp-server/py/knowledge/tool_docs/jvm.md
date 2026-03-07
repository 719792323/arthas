# jvm 命令

查看当前 JVM 信息，包括运行时参数、类加载信息、编译信息、垃圾回收信息、内存信息等。

## 使用方式

```bash
jvm
```

直接执行即可，无需额外参数。输出当前 JVM 的全部运行信息。

## 输出内容说明

### RUNTIME（运行时信息）

| 字段 | 说明 |
|------|------|
| MACHINE-NAME | JVM 进程 ID@主机名 |
| JVM-START-TIME | JVM 启动时间 |
| MANAGEMENT-SPEC-VERSION | JMX 规范版本 |
| SPEC-NAME | JVM 规范名称 |
| SPEC-VENDOR | JVM 规范厂商 |
| SPEC-VERSION | JVM 规范版本 |
| VM-NAME | 虚拟机名称 |
| VM-VENDOR | 虚拟机厂商 |
| VM-VERSION | 虚拟机版本 |
| INPUT-ARGUMENTS | JVM 启动参数 |
| CLASS-PATH | 类路径 |
| BOOT-CLASS-PATH | 引导类路径 |
| LIBRARY-PATH | 本地库路径 |

### CLASS-LOADING（类加载信息）

| 字段 | 说明 |
|------|------|
| LOADED-CLASS-COUNT | 当前已加载类数量 |
| TOTAL-LOADED-CLASS-COUNT | 总共加载过的类数量 |
| UNLOADED-CLASS-COUNT | 已卸载的类数量 |

### MEMORY（内存信息）

| 字段 | 说明 |
|------|------|
| HEAP-MEMORY-USAGE | 堆内存使用情况（init/used/committed/max） |
| NO-HEAP-MEMORY-USAGE | 非堆内存使用情况 |
| PENDING-FINALIZE-COUNT | 等待 finalize 的对象数 |

### GARBAGE-COLLECTORS（垃圾回收信息）

显示各个垃圾回收器的名称和回收次数/时间：

| 字段 | 说明 |
|------|------|
| GC-NAME | 垃圾回收器名称（如 G1 Young、G1 Old） |
| COLLECTION-COUNT | 垃圾回收次数 |
| COLLECTION-TIME | 垃圾回收总耗时（毫秒） |

### THREAD（线程信息）

| 字段 | 说明 |
|------|------|
| COUNT | 当前活跃线程数 |
| DAEMON-COUNT | 守护线程数 |
| PEAK-COUNT | 峰值线程数 |
| STARTED-COUNT | 累计启动过的线程数 |
| DEADLOCK-COUNT | 死锁线程数（0 为正常） |

## 适用场景

- 快速了解 JVM 整体运行状态
- 排查内存泄漏时查看堆/非堆内存趋势
- 排查 GC 问题时查看各回收器的统计数据
- 排查类加载问题时查看已加载/已卸载类数量
