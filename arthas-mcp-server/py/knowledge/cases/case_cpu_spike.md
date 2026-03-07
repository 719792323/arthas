# 案例：线上应用 CPU 突然飙高

## 问题描述

某电商服务在促销期间，CPU 使用率突然从 30% 飙升到 95%，响应时间从 50ms 上升到 5000ms 以上，用户大量投诉无法下单。

## 环境信息

- JDK 版本：JDK 11
- 应用框架：Spring Boot 2.7 + MyBatis
- JVM 参数：`-Xms4g -Xmx4g -XX:+UseG1GC`
- 部署方式：Kubernetes Pod

## 诊断过程

### 使用的工具链

1. `thread -n 5` → 定位高 CPU 线程
2. `watch` → 观察可疑方法参数
3. `trace` → 分析方法调用链耗时

### 诊断步骤

#### Step 1：thread -n 5 定位热点线程

```bash
$ thread -n 5
"http-nio-8080-exec-23" Id=145 cpuUsage=45.32% deltaTime=906ms time=32457ms RUNNABLE
    at java.util.regex.Pattern$GroupHead.match(Pattern.java:4804)
    at java.util.regex.Pattern$Loop.match(Pattern.java:4911)
    at java.util.regex.Pattern$GroupTail.match(Pattern.java:4839)
    at java.util.regex.Pattern$BranchConn.match(Pattern.java:4670)
    at java.util.regex.Pattern$CharProperty.match(Pattern.java:3778)
    ...
    at com.example.service.OrderService.validateAddress(OrderService.java:156)
```

发现多个 HTTP 线程卡在 `java.util.regex.Pattern` 的正则匹配中。

#### Step 2：watch 观察问题参数

```bash
$ watch com.example.service.OrderService validateAddress "{params}" -x 2
method=com.example.service.OrderService.validateAddress
@Object[][
    @String["中国广东省深圳市南山区xxxx路xxxx号xxxx大厦xxxx层xxxx室（详细地址请填写完整）...（超长字符串，2000+字符）"],
]
```

发现用户提交了一个 2000+ 字符的超长地址，触发了正则回溯爆炸。

#### Step 3：确认根因

`OrderService.validateAddress()` 方法使用了一个复杂的地址校验正则表达式，当输入超长字符串时，正则引擎发生灾难性回溯（catastrophic backtracking），导致 CPU 持续满载。

## 最终结论

**根因**：地址校验的正则表达式存在灾难性回溯问题，当用户输入超长地址字符串时，正则匹配耗时指数级增长。

**解决方案**：
1. 在正则校验前增加字符串长度限制（最大 200 字符）
2. 将复杂正则替换为非回溯正则（使用 possessive quantifiers）
3. 增加正则匹配超时机制

**修复后效果**：CPU 使用率恢复到 25%，P99 响应时间 < 100ms。
