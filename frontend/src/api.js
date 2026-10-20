// api.js
import { fetchEventSource } from '@microsoft/fetch-event-source';

const API_BASE = "http://localhost:8000/api";
const TASK_STORAGE_KEY = 'current_task_id';

const connectSSE = (threadId, callbacks, lastEventId = null) => {
  let isFinished = false;
  let lastMessageTime = Date.now();

  let url = `${API_BASE}/stream/${threadId}`;
  if (lastEventId) {
    url += `?from=${lastEventId}`;
  }

  const ctrl = new AbortController();

  // 看门狗：60秒没收到数据才判定后端失联。
  // 阈值不能用 8 秒——图像生成等节点单次执行就可能耗时 60s+，期间没有业务事件，
  // 只能靠心跳续命；阈值过小会在节点正常执行时误判断连并 abort。
  const watchdog = setInterval(() => {
    if (Date.now() - lastMessageTime > 60000) {
      clearInterval(watchdog);
      if (!isFinished) {
        if (callbacks.onError) {
          callbacks.onError("⚠️ 后端服务断开，任务已中断。请刷新页面尝试恢复进度。");
        }
        ctrl.abort();
      }
    }
  }, 2000);

  fetchEventSource(url, {
    method: 'GET',
    signal: ctrl.signal,
    onmessage(ev) {
      lastMessageTime = Date.now();
      try {
        const chunk = JSON.parse(ev.data);
        const type = chunk.type;
        if (type === 'heartbeat') return;

        if (ev.id) lastEventId = ev.id;

        if (type === 'title_chunk' && callbacks.onTitle) callbacks.onTitle(chunk.content);
        else if (type === 'body_chunk' && callbacks.onBody) callbacks.onBody(chunk.content);
        else if (type === 'log' && callbacks.onLog) callbacks.onLog(chunk.content);
        else if (type === 'finished') {
          isFinished = true;
          clearInterval(watchdog);
          if (callbacks.onFinished) callbacks.onFinished(chunk);
          ctrl.abort();
        }
        else if (type === 'error' && callbacks.onError) {
          isFinished = true;
          clearInterval(watchdog);
          callbacks.onError(chunk.message);
          ctrl.abort();
        }
      } catch (e) {
        console.error("解析 SSE 失败:", e);
      }
    },
    onclose() {
      clearInterval(watchdog);
      if (isFinished) {
        if (callbacks.onClose) callbacks.onClose();
      } else {
        if (callbacks.onError) {
          callbacks.onError("⚠️ 后端服务断开，任务已中断。请刷新页面尝试恢复进度。");
        }
      }
      throw new Error('Stop default retry');
    },
    onerror(err) {
      clearInterval(watchdog);
      if (isFinished) {
        throw new Error('Stop default retry');
      }
      if (callbacks.onError) {
        callbacks.onError("⚠️ 网络断开或后端服务崩溃，任务已中断。请刷新页面尝试恢复进度。");
      }
      throw new Error('Stop default retry');
    }
  });

  return ctrl;
};

// 启动工作流
export const startWorkflow = async (payload, callbacks) => {
  try {
    // 🔥 如果后端断开，fetch 会直接抛出异常，被这里捕获
    const res = await fetch(`${API_BASE}/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      if (callbacks.onError) callbacks.onError(errData.detail || "启动任务失败");
      return;
    }

    const { thread_id } = await res.json();
    localStorage.setItem(TASK_STORAGE_KEY, thread_id);
    return connectSSE(thread_id, callbacks, "0");
  } catch (err) {
    // 🔥 捕获网络异常，走 onError 回调
    if (callbacks.onError) callbacks.onError("⚠️ 无法连接到后端服务，请检查服务是否已启动。");
  }
};

// 恢复工作流
export const resumeWorkflow = async (threadId, payload, callbacks) => {
  try {
    const res = await fetch(`${API_BASE}/resume/${threadId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      if (callbacks.onError) callbacks.onError(errData.detail || "恢复任务失败");
      return;
    }

    localStorage.setItem(TASK_STORAGE_KEY, threadId);
    return connectSSE(threadId, callbacks, "$");
  } catch (err) {
    // 🔥 捕获网络异常，走 onError 回调
    if (callbacks.onError) callbacks.onError("⚠️ 无法连接到后端服务，请检查服务是否已启动。");
  }
};

// 页面刷新后重连任务
export const reconnectWorkflow = (callbacks) => {
  const threadId = localStorage.getItem(TASK_STORAGE_KEY);
  if (!threadId) return null;
  return connectSSE(threadId, callbacks, "0");
};

export const clearCurrentTask = () => {
  localStorage.removeItem(TASK_STORAGE_KEY);
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