package com.taobao.arthas.mcp.server.demo;


import java.util.concurrent.TimeUnit;

/**
 * CPU高负载问题测试用例
 * 适合测试：thread、jstack、profiler 等命令
 */
public class Case1 {
    
    private static volatile boolean running = true;
    
    public static void main(String[] args) throws Exception {
        System.out.println("启动CPU密集型线程...");
        
        // 启动多个CPU密集型线程
        for (int i = 0; i < 3; i++) {
            final int threadId = i;
            Thread thread = new Thread(() -> {
                cpuIntensiveTask(threadId);
            }, "case1-" + i);
            thread.start();
        }
        
        // 主线程保持运行
        while (running) {
            TimeUnit.SECONDS.sleep(10);
        }
    }
    
    /**
     * CPU密集型任务 - 死循环计算
     */
    private static void cpuIntensiveTask(int threadId) {
        long count = 0;
        while (running) {
            // 模拟复杂计算
            count++;
            double result = Math.sqrt(count) * Math.sin(count) * Math.cos(count);
            
            // 每100万次打印一次，避免输出过多
            if (count % 1000000 == 0) {
                System.out.println("Thread-" + threadId + " 计算次数: " + count + ", 结果: " + result);
            }
        }
    }
    
    /**
     * 停止所有任务
     */
    public static void stop() {
        running = false;
        System.out.println("停止所有CPU密集型任务");
    }
}
