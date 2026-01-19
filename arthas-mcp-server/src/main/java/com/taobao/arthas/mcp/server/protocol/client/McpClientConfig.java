/*
 * Copyright 2024-2024 the original author or authors.
 */

package com.taobao.arthas.mcp.server.protocol.client;

import com.taobao.arthas.mcp.server.util.Assert;

/**
 * MCP Client 配置类
 * 
 * 支持通过环境变量配置反向连接参数，用于 HTTP/SSE 模式
 * 
 * 环境变量说明：
 * - ARTHAS_MCP_CLIENT_SERVER_URL: 管控平台 HTTP 地址，如 http://localhost:8080/mcp
 * - ARTHAS_MCP_CLIENT_AUTH_TOKEN: 认证 Token
 * - ARTHAS_MCP_CLIENT_RECONNECT_ENABLED: 是否启用自动重连（默认 true）
 * - ARTHAS_MCP_CLIENT_RECONNECT_INITIAL_DELAY: 重连初始延迟毫秒（默认 5000）
 * - ARTHAS_MCP_CLIENT_RECONNECT_MAX_DELAY: 重连最大延迟毫秒（默认 300000）
 * - ARTHAS_MCP_CLIENT_RECONNECT_MULTIPLIER: 重连延迟倍数（默认 2.0）
 * - ARTHAS_MCP_CLIENT_HEARTBEAT_ENABLED: 是否启用心跳（默认 true）
 * - ARTHAS_MCP_CLIENT_HEARTBEAT_INTERVAL: 心跳间隔毫秒（默认 30000）
 * - ARTHAS_MCP_CLIENT_HEARTBEAT_TIMEOUT: 心跳超时毫秒（默认 10000）
 * - ARTHAS_MCP_CLIENT_CONNECT_TIMEOUT: 连接超时毫秒（默认 10000）
 * - ARTHAS_MCP_CLIENT_REQUEST_TIMEOUT: 请求超时毫秒（默认 30000）
 * - ARTHAS_MCP_CLIENT_SSE_RECONNECT_DELAY: SSE 重连延迟毫秒（默认 3000）
 * - ARTHAS_MCP_CLIENT_CLIENT_NAME: 客户端名称（默认 arthas-mcp-client）
 * - ARTHAS_MCP_CLIENT_CLIENT_VERSION: 客户端版本（默认 4.1.5）
 *
 * @author Arthas Team
 */
public class McpClientConfig {

    // ========== 环境变量名 ==========
    public static final String ENV_SERVER_URL = "ARTHAS_MCP_CLIENT_SERVER_URL";
    public static final String ENV_AUTH_TOKEN = "ARTHAS_MCP_CLIENT_AUTH_TOKEN";
    public static final String ENV_RECONNECT_ENABLED = "ARTHAS_MCP_CLIENT_RECONNECT_ENABLED";
    public static final String ENV_RECONNECT_INITIAL_DELAY = "ARTHAS_MCP_CLIENT_RECONNECT_INITIAL_DELAY";
    public static final String ENV_RECONNECT_MAX_DELAY = "ARTHAS_MCP_CLIENT_RECONNECT_MAX_DELAY";
    public static final String ENV_RECONNECT_MULTIPLIER = "ARTHAS_MCP_CLIENT_RECONNECT_MULTIPLIER";
    public static final String ENV_HEARTBEAT_ENABLED = "ARTHAS_MCP_CLIENT_HEARTBEAT_ENABLED";
    public static final String ENV_HEARTBEAT_INTERVAL = "ARTHAS_MCP_CLIENT_HEARTBEAT_INTERVAL";
    public static final String ENV_HEARTBEAT_TIMEOUT = "ARTHAS_MCP_CLIENT_HEARTBEAT_TIMEOUT";
    public static final String ENV_CONNECT_TIMEOUT = "ARTHAS_MCP_CLIENT_CONNECT_TIMEOUT";
    public static final String ENV_REQUEST_TIMEOUT = "ARTHAS_MCP_CLIENT_REQUEST_TIMEOUT";
    public static final String ENV_SSE_RECONNECT_DELAY = "ARTHAS_MCP_CLIENT_SSE_RECONNECT_DELAY";
    public static final String ENV_CLIENT_NAME = "ARTHAS_MCP_CLIENT_CLIENT_NAME";
    public static final String ENV_CLIENT_VERSION = "ARTHAS_MCP_CLIENT_CLIENT_VERSION";

    // ========== 默认值 ==========
    private static final String DEFAULT_CLIENT_NAME = "arthas-mcp-client";
    private static final String DEFAULT_CLIENT_VERSION = "4.1.5";
    private static final long DEFAULT_CONNECT_TIMEOUT = 10000;
    private static final long DEFAULT_REQUEST_TIMEOUT = 30000;
    private static final long DEFAULT_SSE_RECONNECT_DELAY = 3000;

    // ========== 配置字段 ==========
    private String serverUrl;
    private String authToken;
    private ReconnectConfig reconnect = new ReconnectConfig();
    private HeartbeatConfig heartbeat = new HeartbeatConfig();
    private long connectTimeout = DEFAULT_CONNECT_TIMEOUT;
    private long requestTimeout = DEFAULT_REQUEST_TIMEOUT;
    private long sseReconnectDelay = DEFAULT_SSE_RECONNECT_DELAY;
    private String clientName = DEFAULT_CLIENT_NAME;
    private String clientVersion = DEFAULT_CLIENT_VERSION;

    /**
     * 重连配置
     */
    public static class ReconnectConfig {
        private boolean enabled = true;
        private long initialDelay = 5000;
        private long maxDelay = 300000;
        private double multiplier = 2.0;

        public boolean isEnabled() {
            return enabled;
        }

        public void setEnabled(boolean enabled) {
            this.enabled = enabled;
        }

        public long getInitialDelay() {
            return initialDelay;
        }

        public void setInitialDelay(long initialDelay) {
            this.initialDelay = initialDelay;
        }

        public long getMaxDelay() {
            return maxDelay;
        }

        public void setMaxDelay(long maxDelay) {
            this.maxDelay = maxDelay;
        }

        public double getMultiplier() {
            return multiplier;
        }

        public void setMultiplier(double multiplier) {
            this.multiplier = multiplier;
        }

        @Override
        public String toString() {
            return "ReconnectConfig{" +
                    "enabled=" + enabled +
                    ", initialDelay=" + initialDelay +
                    ", maxDelay=" + maxDelay +
                    ", multiplier=" + multiplier +
                    '}';
        }
    }

    /**
     * 心跳配置
     */
    public static class HeartbeatConfig {
        private boolean enabled = true;
        private long interval = 30000;
        private long timeout = 10000;

        public boolean isEnabled() {
            return enabled;
        }

        public void setEnabled(boolean enabled) {
            this.enabled = enabled;
        }

        public long getInterval() {
            return interval;
        }

        public void setInterval(long interval) {
            this.interval = interval;
        }

        public long getTimeout() {
            return timeout;
        }

        public void setTimeout(long timeout) {
            this.timeout = timeout;
        }

        @Override
        public String toString() {
            return "HeartbeatConfig{" +
                    "enabled=" + enabled +
                    ", interval=" + interval +
                    ", timeout=" + timeout +
                    '}';
        }
    }

    /**
     * 从环境变量加载配置
     */
    public static McpClientConfig fromEnvironment() {
        McpClientConfig config = new McpClientConfig();
        
        // 服务端地址
        String serverUrl = getEnv(ENV_SERVER_URL);
        if (serverUrl != null && !serverUrl.isEmpty()) {
            config.setServerUrl(serverUrl);
        }
        
        // 认证 Token
        String authToken = getEnv(ENV_AUTH_TOKEN);
        if (authToken != null && !authToken.isEmpty()) {
            config.setAuthToken(authToken);
        }
        
        // 重连配置
        String reconnectEnabled = getEnv(ENV_RECONNECT_ENABLED);
        if (reconnectEnabled != null && !reconnectEnabled.isEmpty()) {
            config.getReconnect().setEnabled(Boolean.parseBoolean(reconnectEnabled));
        }
        
        String reconnectInitialDelay = getEnv(ENV_RECONNECT_INITIAL_DELAY);
        if (reconnectInitialDelay != null && !reconnectInitialDelay.isEmpty()) {
            config.getReconnect().setInitialDelay(Long.parseLong(reconnectInitialDelay));
        }
        
        String reconnectMaxDelay = getEnv(ENV_RECONNECT_MAX_DELAY);
        if (reconnectMaxDelay != null && !reconnectMaxDelay.isEmpty()) {
            config.getReconnect().setMaxDelay(Long.parseLong(reconnectMaxDelay));
        }
        
        String reconnectMultiplier = getEnv(ENV_RECONNECT_MULTIPLIER);
        if (reconnectMultiplier != null && !reconnectMultiplier.isEmpty()) {
            config.getReconnect().setMultiplier(Double.parseDouble(reconnectMultiplier));
        }
        
        // 心跳配置
        String heartbeatEnabled = getEnv(ENV_HEARTBEAT_ENABLED);
        if (heartbeatEnabled != null && !heartbeatEnabled.isEmpty()) {
            config.getHeartbeat().setEnabled(Boolean.parseBoolean(heartbeatEnabled));
        }
        
        String heartbeatInterval = getEnv(ENV_HEARTBEAT_INTERVAL);
        if (heartbeatInterval != null && !heartbeatInterval.isEmpty()) {
            config.getHeartbeat().setInterval(Long.parseLong(heartbeatInterval));
        }
        
        String heartbeatTimeout = getEnv(ENV_HEARTBEAT_TIMEOUT);
        if (heartbeatTimeout != null && !heartbeatTimeout.isEmpty()) {
            config.getHeartbeat().setTimeout(Long.parseLong(heartbeatTimeout));
        }
        
        // 连接超时
        String connectTimeout = getEnv(ENV_CONNECT_TIMEOUT);
        if (connectTimeout != null && !connectTimeout.isEmpty()) {
            config.setConnectTimeout(Long.parseLong(connectTimeout));
        }
        
        // 请求超时
        String requestTimeout = getEnv(ENV_REQUEST_TIMEOUT);
        if (requestTimeout != null && !requestTimeout.isEmpty()) {
            config.setRequestTimeout(Long.parseLong(requestTimeout));
        }
        
        // SSE 重连延迟
        String sseReconnectDelay = getEnv(ENV_SSE_RECONNECT_DELAY);
        if (sseReconnectDelay != null && !sseReconnectDelay.isEmpty()) {
            config.setSseReconnectDelay(Long.parseLong(sseReconnectDelay));
        }
        
        // 客户端信息
        String clientName = getEnv(ENV_CLIENT_NAME);
        if (clientName != null && !clientName.isEmpty()) {
            config.setClientName(clientName);
        }
        
        String clientVersion = getEnv(ENV_CLIENT_VERSION);
        if (clientVersion != null && !clientVersion.isEmpty()) {
            config.setClientVersion(clientVersion);
        }
        
        return config;
    }

    private static String getEnv(String name) {
        return System.getenv(name);
    }

    /**
     * 验证配置有效性
     */
    public void validate() {
        Assert.hasText(serverUrl, "serverUrl must not be empty");
        
        // 验证 URL 格式
        if (!serverUrl.startsWith("http://") && !serverUrl.startsWith("https://")) {
            throw new IllegalArgumentException("serverUrl must start with http:// or https://");
        }
    }

    /**
     * 是否使用 HTTPS
     */
    public boolean isSecure() {
        return serverUrl != null && serverUrl.startsWith("https://");
    }

    // ========== Getters and Setters ==========

    public String getServerUrl() {
        return serverUrl;
    }

    public void setServerUrl(String serverUrl) {
        this.serverUrl = serverUrl;
    }

    public String getAuthToken() {
        return authToken;
    }

    public void setAuthToken(String authToken) {
        this.authToken = authToken;
    }

    public ReconnectConfig getReconnect() {
        return reconnect;
    }

    public void setReconnect(ReconnectConfig reconnect) {
        this.reconnect = reconnect;
    }

    public HeartbeatConfig getHeartbeat() {
        return heartbeat;
    }

    public void setHeartbeat(HeartbeatConfig heartbeat) {
        this.heartbeat = heartbeat;
    }

    public long getConnectTimeout() {
        return connectTimeout;
    }

    public void setConnectTimeout(long connectTimeout) {
        this.connectTimeout = connectTimeout;
    }

    public long getRequestTimeout() {
        return requestTimeout;
    }

    public void setRequestTimeout(long requestTimeout) {
        this.requestTimeout = requestTimeout;
    }

    public long getSseReconnectDelay() {
        return sseReconnectDelay;
    }

    public void setSseReconnectDelay(long sseReconnectDelay) {
        this.sseReconnectDelay = sseReconnectDelay;
    }

    public String getClientName() {
        return clientName;
    }

    public void setClientName(String clientName) {
        this.clientName = clientName;
    }

    public String getClientVersion() {
        return clientVersion;
    }

    public void setClientVersion(String clientVersion) {
        this.clientVersion = clientVersion;
    }

    @Override
    public String toString() {
        return "McpClientConfig{" +
                "serverUrl='" + serverUrl + '\'' +
                ", authToken='" + (authToken != null ? "***" : "null") + '\'' +
                ", reconnect=" + reconnect +
                ", heartbeat=" + heartbeat +
                ", connectTimeout=" + connectTimeout +
                ", requestTimeout=" + requestTimeout +
                ", sseReconnectDelay=" + sseReconnectDelay +
                ", clientName='" + clientName + '\'' +
                ", clientVersion='" + clientVersion + '\'' +
                '}';
    }

    /**
     * Builder 模式
     */
    public static Builder builder() {
        return new Builder();
    }

    public static class Builder {
        private final McpClientConfig config = new McpClientConfig();

        public Builder serverUrl(String serverUrl) {
            config.setServerUrl(serverUrl);
            return this;
        }

        public Builder authToken(String authToken) {
            config.setAuthToken(authToken);
            return this;
        }

        public Builder reconnectEnabled(boolean enabled) {
            config.getReconnect().setEnabled(enabled);
            return this;
        }

        public Builder reconnectInitialDelay(long delay) {
            config.getReconnect().setInitialDelay(delay);
            return this;
        }

        public Builder reconnectMaxDelay(long delay) {
            config.getReconnect().setMaxDelay(delay);
            return this;
        }

        public Builder reconnectMultiplier(double multiplier) {
            config.getReconnect().setMultiplier(multiplier);
            return this;
        }

        public Builder heartbeatEnabled(boolean enabled) {
            config.getHeartbeat().setEnabled(enabled);
            return this;
        }

        public Builder heartbeatInterval(long interval) {
            config.getHeartbeat().setInterval(interval);
            return this;
        }

        public Builder heartbeatTimeout(long timeout) {
            config.getHeartbeat().setTimeout(timeout);
            return this;
        }

        public Builder connectTimeout(long timeout) {
            config.setConnectTimeout(timeout);
            return this;
        }

        public Builder requestTimeout(long timeout) {
            config.setRequestTimeout(timeout);
            return this;
        }

        public Builder sseReconnectDelay(long delay) {
            config.setSseReconnectDelay(delay);
            return this;
        }

        public Builder clientName(String name) {
            config.setClientName(name);
            return this;
        }

        public Builder clientVersion(String version) {
            config.setClientVersion(version);
            return this;
        }

        public McpClientConfig build() {
            config.validate();
            return config;
        }
    }
}
