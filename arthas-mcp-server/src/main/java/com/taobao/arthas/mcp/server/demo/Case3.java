package com.taobao.arthas.mcp.server.demo;

import java.util.concurrent.TimeUnit;

/**
 * 死锁问题测试用例
 * 适合测试：thread、jstack 等命令
 */
public class Case3 {
    
    private static final Object LOCK_A = new Object();
    private static final Object LOCK_B = new Object();
    
    public static void main(String[] args) throws Exception {
        System.out.println("准备触发死锁...");
        
        // 线程1：先锁A再锁B
        Thread thread1 = new Thread(() -> {
            synchronized (LOCK_A) {
                System.out.println("Thread-1 获得 LOCK_A");
                
                try {
                    TimeUnit.SECONDS.sleep(1);
                } catch (InterruptedException e) {
                    e.printStackTrace();
                }
                
                System.out.println("Thread-1 尝试获取 LOCK_B...");
                synchronized (LOCK_B) {
                    System.out.println("Thread-1 获得 LOCK_B");
                }
            }
        }, "case3-thread-1");
        
        // 线程2：先锁B再锁A
        Thread thread2 = new Thread(() -> {
            synchronized (LOCK_B) {
                System.out.println("Thread-2 获得 LOCK_B");
                
                try {
                    TimeUnit.SECONDS.sleep(1);
                } catch (InterruptedException e) {
                    e.printStackTrace();
                }
                
                System.out.println("Thread-2 尝试获取 LOCK_A...");
                synchronized (LOCK_A) {
                    System.out.println("Thread-2 获得 LOCK_A");
                }
            }
        }, "case3-thread-2");
        
        thread1.start();
        thread2.start();
        
        // 等待死锁发生
        TimeUnit.SECONDS.sleep(3);
        System.out.println("死锁已触发！使用 'thread' 或 'jstack' 命令查看");
        
        // 主线程保持运行
        thread1.join();
        thread2.join();
    }
}
