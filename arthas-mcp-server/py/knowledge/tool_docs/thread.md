# thread 命令

查看当前线程信息，查找本应用最忙的线程。

## 使用方式

### 查看所有线程信息

```bash
thread
```

显示所有线程的 CPU 使用率、状态等信息。输出包括线程 ID、名称、分组、优先级、状态、CPU 使用率等。

### 查看指定线程的堆栈

```bash
thread <id>
```

查看指定线程 ID 的堆栈信息，用于分析某个线程正在执行的方法调用链。

### 查找最忙线程

```bash
thread -n 3
```

列出最忙的前 N 个线程并打印堆栈。适用于 CPU 使用率高时快速定位热点线程。

### 查找阻塞线程

```bash
thread -b
```

找出当前阻塞其他线程的线程。当出现死锁或线程等待问题时，此选项非常有用。

### 指定采样间隔

```bash
thread -i 1000
```

指定 CPU 使用率统计的采样间隔（毫秒），默认 200ms。更长的采样间隔可以提高统计准确性。

## 参数说明

| 参数名 | 参数说明 |
|--------|---------|
| id | 线程 ID，查看指定线程的堆栈 |
| -n | 指定最忙线程的前 N 个，默认按 CPU 使用率排序 |
| -b | 找出阻塞其他线程的线程（死锁检测） |
| -i | 指定 CPU 使用率的采样间隔（毫秒），默认 200ms |
| --state | 查看指定状态的线程，如 RUNNABLE、WAITING 等 |

## 输出字段说明

| 字段 | 说明 |
|------|------|
| ID | 线程 ID |
| NAME | 线程名称 |
| GROUP | 线程组 |
| PRIORITY | 线程优先级 |
| STATE | 线程状态（RUNNABLE/WAITING/TIMED_WAITING/BLOCKED） |
| %CPU | CPU 使用率 |
| DELTA_TIME | 上次采样以来的增量时间 |
| TIME | 总运行时间 |

## 使用示例

### 找出 CPU 使用率最高的 3 个线程

```bash
$ thread -n 3
"main" Id=1 cpuUsage=98.42% deltaTime=198ms time=3457ms RUNNABLE
    at com.example.App.hotMethod(App.java:15)
    at com.example.App.main(App.java:8)
```

### 查看 BLOCKED 状态的线程

```bash
$ thread --state BLOCKED
Threads Total: 32, NEW: 0, RUNNABLE: 10, BLOCKED: 2
```
