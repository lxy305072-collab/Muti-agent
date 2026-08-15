// api.js
import { fetchEventSource } from '@microsoft/fetch-event-source';

const API_BASE = "http://localhost:8000/api";

export const startWorkflow = (payload, callbacks) => {
  let isFinished = false; // 标志位
  return fetchEventSource(`${API_BASE}/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    onmessage(ev) {
      try {
        const chunk = JSON.parse(ev.data);
        const type = chunk.type;

        if (type === 'title_chunk' && callbacks.onTitle) callbacks.onTitle(chunk.content);
        else if (type === 'body_chunk' && callbacks.onBody) callbacks.onBody(chunk.content);
        else if (type === 'log' && callbacks.onLog) callbacks.onLog(chunk.content);
        else if (type === 'finished') {
          isFinished = true; // 标记为正常结束
          if (callbacks.onFinished) callbacks.onFinished(chunk);
        }
        else if (type === 'error' && callbacks.onError) callbacks.onError(chunk.message);
      } catch (e) {
        console.error("解析 SSE 失败:", e);
      }
    },
    onclose() {
      // 如果后端已经发送了 finished 事件，说明是正常结束，不报错
      if (!isFinished && callbacks.onError) {
        callbacks.onError("连接异常断开 (后端可能崩溃了)");
      }
      if (callbacks.onClose) callbacks.onClose();
    },
  });
};

// 恢复工作流 (人工审核通过/打回)
export const resumeWorkflow = (threadId, payload, callbacks) => {
  let isFinished = false; // 标志位
  return fetchEventSource(`${API_BASE}/resume/${threadId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    onmessage(ev) {
      try {
        const chunk = JSON.parse(ev.data);
        const type = chunk.type;

        if (type === 'title_chunk' && callbacks.onTitle) callbacks.onTitle(chunk.content);
        else if (type === 'body_chunk' && callbacks.onBody) callbacks.onBody(chunk.content);
        else if (type === 'log' && callbacks.onLog) callbacks.onLog(chunk.content);
        else if (type === 'finished') {
          isFinished = true; // 标记为正常结束
          if (callbacks.onFinished) callbacks.onFinished(chunk);
        }
        else if (type === 'error' && callbacks.onError) callbacks.onError(chunk.message);
      } catch (e) {
        console.error("解析 SSE 失败:", e);
      }
    },
    onclose() {
      if (!isFinished && callbacks.onError) {
        callbacks.onError("连接异常断开 (后端可能崩溃了)");
      }
      if (callbacks.onClose) callbacks.onClose();
    },
  });
};

// 获取历史物料
export const getHistory = async () => {
  const res = await fetch(`${API_BASE}/history`);
  return res.json();
};

// 提交用户打分
export const rateMaterial = async (threadId, score) => {
  const res = await fetch(`${API_BASE}/rate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ thread_id: threadId, score: score })
  });
  return res.json();
};

// 删除历史物料
export const deleteMaterial = async (threadId) => {
  const res = await fetch(`${API_BASE}/history/${threadId}`, {
    method: 'DELETE'
  });
  return res.json();
};