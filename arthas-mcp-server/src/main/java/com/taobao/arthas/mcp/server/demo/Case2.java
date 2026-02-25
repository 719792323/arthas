package com.taobao.arthas.mcp.server.demo;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeUnit;

/**
 * 内存泄漏问题测试用例
 * 适合测试：heapdump、memory、vmtool 等命令
 */
public class Case2 {
    
    // 静态集合持有对象引用，导致无法GC
    private static final List<byte[]> LEAK_LIST = new ArrayList<>();
    private static volatile boolean running = true;
    
    public static void main(String[] args) throws Exception {
        System.out.println("开始模拟内存泄漏...");
        
        // 启动内存泄漏线程
        Thread leakThread = new Thread(() -> {
            memoryLeakTask();
        }, "case2-thread");
        leakThread.start();
        
        // 主线程保持运行
        while (running) {
            TimeUnit.SECONDS.sleep(5);
            System.out.println("当前泄漏对象数量: " + LEAK_LIST.size() + 
                             ", 预估内存占用: " + (LEAK_LIST.size() * 1024 / 1024) + " MB");
        }
    }
    
    /**
     * 内存泄漏任务 - 不断创建对象并持有引用
     */
    private static void memoryLeakTask() {
        int count = 0;
        while (running) {
            try {
                // 每次创建1MB的数组
                byte[] leak = new byte[1024 * 1024];
                LEAK_LIST.add(leak);
                count++;
                
                // 每10个对象打印一次
                if (count % 10 == 0) {
                    System.out.println("已创建 " + count + " 个泄漏对象");
                }
                
                // 控制速度，避免太快OOM
                TimeUnit.MILLISECONDS.sleep(500);
                
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                break;
            } catch (OutOfMemoryError e) {
                System.err.println("内存溢出！已创建对象数: " + count);
                break;
            }
        }
    }
    
    /**
     * 清理泄漏对象
     */
    public static void cleanup() {
        LEAK_LIST.clear();
        System.gc();
        System.out.println("已清理泄漏对象并触发GC");
    }
    
    /**
     * 停止任务
     */
    public static void stop() {
        running = false;
        System.out.println("停止内存泄漏任务");
    }
}
