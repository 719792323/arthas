# watch 命令

方法执行数据观测，让你能方便地观察到指定方法的调用情况。能观察到的范围包括：方法入参、返回值、抛出异常、方法内部变量等。

## 使用方式

### 观察方法出参和返回值

```bash
watch com.example.UserService getUser "{params, returnObj}" -x 2
```

### 观察方法入参

```bash
watch com.example.UserService getUser "{params}" -b
```

### 观察异常信息

```bash
watch com.example.UserService getUser "{params, throwExp}" -e
```

### 条件表达式过滤

```bash
watch com.example.UserService getUser "{params, returnObj}" "params[0] > 100" -x 2
```

### 按耗时过滤

```bash
watch com.example.UserService getUser "{params, returnObj}" '#cost>200'
```

## 参数说明

| 参数名 | 参数说明 |
|--------|---------|
| class-pattern | 类名表达式匹配（支持通配符 `*`） |
| method-pattern | 方法名表达式匹配（支持通配符 `*`） |
| express | 观察表达式，使用 OGNL 表达式语法 |
| condition-express | 条件表达式，满足条件时才输出 |
| -b | 在方法调用之前观察（before） |
| -e | 在方法抛出异常之后观察（exception） |
| -s | 在方法返回之后观察（success） |
| -f | 在方法结束之后观察（finish，包含正常返回和异常返回，默认） |
| -x | 指定输出结果的属性遍历深度，默认为 1 |
| -n | 指定执行次数 |
| #cost | 方法执行耗时（毫秒），用于条件过滤 |

## OGNL 表达式常用变量

| 变量名 | 说明 |
|--------|------|
| params | 方法入参数组 |
| returnObj | 方法返回值 |
| throwExp | 抛出的异常对象 |
| target | 当前对象实例（this） |
| clazz | 当前类的 Class 对象 |

## 使用示例

### 观察 HashMap 的 put 方法

```bash
$ watch java.util.HashMap put "{params, returnObj}" -x 2
method=java.util.HashMap.put location=AtExit
ts=2024-01-15 10:30:45; [cost=0.0536ms]
@ArrayList[
    @Object[][
        @String[key1],
        @String[value1],
    ],
    null,
]
```

### 观察耗时超过 200ms 的方法

```bash
$ watch com.example.Service slowMethod "{params, returnObj}" '#cost>200' -x 2
```

## 适用场景

- 观察方法的入参和返回值，排查数据传递问题
- 通过异常观察定位异常产生的具体参数
- 通过耗时过滤定位慢方法的具体调用
- 配合条件表达式精确观察特定场景
