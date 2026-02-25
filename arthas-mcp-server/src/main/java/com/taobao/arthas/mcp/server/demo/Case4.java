package com.taobao.arthas.mcp.server.demo;


import java.util.Random;
import java.util.concurrent.TimeUnit;

/**
 * 频繁异常问题测试用例
 * 适合测试：watch、trace、tt 等命令
 */
public class Case4 {
    
    private static final Random RANDOM = new Random();
    private static volatile boolean running = true;
    private static int successCount = 0;
    private static int exceptionCount = 0;
    
    public static void main(String[] args) throws Exception {
        System.out.println("开始执行异常频繁的方法...");
        
        // 启动异常频繁线程
        Thread exceptionThread = new Thread(() -> {
            while (running) {
                try {
                    executeWithException();
                    TimeUnit.MILLISECONDS.sleep(500);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    break;
                }
            }
        }, "exception-thread");
        exceptionThread.start();
        
        // 统计线程
        Thread statsThread = new Thread(() -> {
            while (running) {
                try {
                    Thread.sleep(1000);
                    System.out.println("统计 - 成功: " + successCount + ", 异常: " + exceptionCount + 
                                     ", 异常率: " + (exceptionCount * 100.0 / (successCount + exceptionCount)) + "%");
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    break;
                }
            }
        }, "stats-thread");
        statsThread.start();
        
        // 主线程保持运行
        while (running) {
            TimeUnit.SECONDS.sleep(10);
        }
    }
    
    /**
     * 执行可能抛出异常的方法
     */
    public static void executeWithException() {
        try {
            // 生成随机用户ID和订单金额
            int userId = RANDOM.nextInt(10);
            double amount = RANDOM.nextDouble() * 10000;
            
            // 调用业务方法
            processOrder(userId, amount);
            
            successCount++;
            
        } catch (NullPointerException e) {
            exceptionCount++;
            System.err.println("捕获到 NullPointerException: " + e.getMessage());
        } catch (IllegalArgumentException e) {
            exceptionCount++;
            System.err.println("捕获到 IllegalArgumentException: " + e.getMessage());
        } catch (RuntimeException e) {
            exceptionCount++;
            System.err.println("捕获到 RuntimeException: " + e.getMessage());
        }
    }
    
    /**
     * 处理订单 - 特定参数会触发异常
     */
    private static void processOrder(int userId, double amount) {
        // 特定用户ID会触发空指针异常（模拟缓存未命中）
        if (userId == 5) {
            getUserInfo(userId);
        }

        // 特定金额范围会触发业务异常（模拟风控规则）
        if (amount > 500) {
            checkRiskControl(amount);
        }
        
        // 正常处理
        normalExecution((int) amount);
    }
    
    /**
     * 获取用户信息 - 特定用户会返回null导致NPE
     */
    private static void getUserInfo(int userId) {
        // 模拟特定用户缓存失效，返回null
        String userInfo = null;
        System.out.println("用户信息长度: " + userInfo.length()); // 触发NPE
    }
    
    /**
     * 验证金额 - 负数会抛出异常
     */
    private static void validateAmount(double amount) {
        if (amount < 0) {
            throw new IllegalArgumentException("订单金额不能为负数: " + amount);
        }
    }
    
    /**
     * 风控检查 - 特定金额范围触发异常
     */
    private static void checkRiskControl(double amount) {
        throw new RuntimeException("触发风控规则，金额异常: " + amount);
    }
    
    /**
     * 正常执行
     */
    private static void normalExecution(int value) {
        // 模拟正常业务逻辑
        double result = Math.sqrt(value) * Math.log(value + 1);
        if (value % 100 == 0) {
            System.out.println("订单处理成功，金额: " + value + ", 处理结果: " + result);
        }
    }
    
    /**
     * 停止任务
     */
    public static void stop() {
        running = false;
        System.out.println("停止异常频繁任务");
        System.out.println("最终统计 - 成功: " + successCount + ", 异常: " + exceptionCount);
    }
}
