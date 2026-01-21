# 无参数命令调用失败问题
## 问题分析

从测试结果可以看到：
- `thread`、`sysprop`、`sc` 这些**有参数的工具**调用成功 ✅
- `jvm`、`memory` 这些**无参数的工具**返回 `toolInput cannot be null or empty` ❌

你的 Python 脚本写法是正确的：
```python
# 示例 1: 查看 JVM 信息
client.call_tool("jvm", {})   # 传递空对象 {}
```

## 问题根源（在 Arthas Java 代码中）

问题出在 Arthas 的服务端代码 [McpToolUtils.java](/Users/songji/Code/Java/arthas/core/src/main/java/com/taobao/arthas/core/mcp/tool/util/McpToolUtils.java)：

```java
private static String convertParametersToString(Map<String, Object> parameters) {
    if (parameters == null) {
        return "";  // ← 这里返回空字符串，导致后续检查失败
    }
    // ...
}
```

当客户端传递空的 `arguments: {}` 时：
1. `parameters` 可能为 `null` 或空 Map
2. 服务端把它转成空字符串 `""`
3. 后面 `DefaultToolCallback.call()` 检查 `Assert.hasText(toolInput, "toolInput cannot be null or empty")` 就会失败

## 已提交的修复

我刚才修改了 Arthas 服务端代码，把 `null` 或空 Map 转成 `"{}"` 而不是空字符串：

```java
private static String convertParametersToString(Map<String, Object> parameters) {
    if (parameters == null || parameters.isEmpty()) {
        return "{}";  // ← 修复：返回空 JSON 对象
    }
    // ...
}
```

## 下一步

你需要**重新编译 Arthas 并重启服务**，然后 Python 测试脚本就能正常工作了：

```bash
# 重新编译
cd /Users/songji/Code/Java/arthas
mvn clean package -DskipTests -pl core -am

# 重启 Arthas 后再运行测试
python arthas-mcp-server/test_mcp_client.py
```