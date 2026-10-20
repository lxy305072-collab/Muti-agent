# 前端 · 营销物料生成工作台

React 19 + Vite 8 + Tailwind CSS 3 实现的前端界面，包含工作台、人工审核弹窗、评分与历史物料画廊。

完整的项目说明、技术架构与快速开始请见 [根目录 README](../README.md)。

## 启动

```bash
npm install
npm run dev
```

开发服务器监听 `http://localhost:5173`，`/api` 请求由 Vite 代理转发到后端 `http://localhost:8000`（见 `vite.config.js`）。

## 关键文件

| 文件 | 说明 |
|---|---|
| `src/App.jsx` | 主界面。工作台表单、Agent 节点进度条、实时日志面板、人工审核弹窗、评分区、历史画廊分页 |
| `src/api.js` | API 封装。基于 `@microsoft/fetch-event-source` 的 SSE 客户端，含 60s 看门狗与断线重连；任务 ID 存于 `localStorage`，刷新页面可恢复进度 |

## 可用命令

| 命令 | 作用 |
|---|---|
| `npm run dev` | 启动开发服务器 |
| `npm run build` | 生产构建 |
| `npm run preview` | 预览生产构建 |
| `npm run lint` | Oxlint 代码检查 |
