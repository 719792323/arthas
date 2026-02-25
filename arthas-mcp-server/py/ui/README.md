# Arthas 诊断中心 - 前端界面

一个用于展示和管理 Arthas 诊断任务过程的 Web 前端界面，基于 React + TypeScript + Tailwind CSS 构建。

## 环境要求

- **Node.js** >= 16.0.0
- **npm** >= 7.0.0（或 pnpm / yarn）
- 后端服务运行在 `http://localhost:8080`（提供 `/api` 接口）

## 快速开始

### 1. 安装依赖

```bash
cd py/ui
npm install
```

### 2. 启动开发服务器

```bash
npm run dev
```

启动后访问 **http://localhost:3000** 即可打开界面。

> 开发模式下，所有 `/api` 请求会自动代理到 `http://localhost:8080`，请确保后端服务已启动。

### 3. 构建生产版本

```bash
npm run build
```

构建产物输出在 `dist/` 目录下，可部署到任意静态文件服务器。

### 4. 预览生产版本

```bash
npm run preview
```