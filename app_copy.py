import streamlit as st
import requests
import json
import time
from streamlit.elements.widgets import feedback

st.set_page_config(page_title="营销物料生成系统 V6", page_icon="📦", layout="wide")
st.title("📦 营销物料自动化生成平台")

API_BASE = "http://localhost:8000/api"

# ================= 初始化 Session State =================
if "thread_id" not in st.session_state:
    st.session_state.thread_id = None
if "status" not in st.session_state:
    st.session_state.status = "idle" # idle, interrupter, finished
if "data" not in st.session_state:
    st.session_state.data = {}

# ================= 侧边栏：产品信息输入 =================
with st.sidebar:
    st.header("🚀 产品信息配置")
    product_name = st.text_input("产品名称","极光智能保温杯")
    key_features = st.text_area("核心卖点", "温显、超长保温、轻量化")
    target_audience = st.text_input("目标受众", "户外爱好者、上班族")

    if st.button("开始生成",type="primary",use_container_width=True):
        # 创建一个状态容器用于展示流式过程
        with st.status("正在生成营销文案...", expanded=True) as status:
            try:
                # 开启流式请求
                res = requests.post(
                    f"{API_BASE}/start",
                    json={
                        "product_name": product_name,
                        "key_features": key_features,
                        "target_audience": target_audience
                    },
                    stream=True  # 🚨 核心参数：开启流式接收
                )

                if res.status_code == 200:
                    st.write("📡 已连接服务端，接收数据中...")

                    # 🔥 优化 1：创建两个占位符，分别用于原地更新标题和正文
                    title_placeholder = st.empty()
                    body_placeholder = st.empty()
                    full_title = ""
                    full_body = ""

                    # 逐行读取 SSE 流
                    for line in res.iter_lines(decode_unicode=True):
                        if line and line.startswith("data: "):
                            # 解析 SSE 数据
                            json_str = line[5:].strip()
                            try:
                                chunk = json.loads(json_str)
                                chunk_type = chunk.get("type")

                                # 1. 接收到文案片段：实时渲染
                                if chunk_type =="title_chunk":
                                    full_title += chunk["content"]
                                    # 🔥 核心修改：在占位符中原地更新 markdown 内容
                                    title_placeholder.markdown(full_title)
                                    # 接收到正文片段：在正文占位符原地更新
                                elif chunk_type == "body_chunk":
                                    full_body += chunk["content"]
                                    body_placeholder.markdown(full_body)

                                # 2. 接收到结束标志：保存状态并关闭容器
                                elif chunk.get("type") == "finished":
                                    st.session_state.thread_id = chunk["thread_id"]
                                    st.session_state.status = chunk["status"]
                                    st.session_state.data = chunk["data"]

                                    status.update(label="✅ 文案生成完毕，等待人工审核", state="complete", expanded=False)

                                elif chunk.get("type") == "error":
                                    status.update(label="❌ 生成失败", state="error")
                                    st.error(chunk.get("message", "未知错误"))
                                # # 🔥 侧边栏不渲染 title_chunk 和 body_chunk，静默等待结束
                                # if chunk.get("type") == "finished":
                                #     st.session_state.thread_id = chunk["thread_id"]
                                #     st.session_state.status = chunk["status"]
                                #     st.session_state.data = chunk["data"]
                                #     status.update(label="✅ 文案生成完毕，等待人工审核", state="complete", expanded=False)
                                #
                                # elif chunk.get("type") == "error":
                                #     status.update(label="❌ 生成失败", state="error")
                                #     st.error(chunk.get("message", "未知错误"))


                            except json.JSONDecodeError:
                                continue
                else:
                    st.error(f"请求失败，状态码: {res.status_code}")

            except requests.exceptions.RequestException as e:
                st.error(f"网络连接异常: {e}")

        # 如果状态已经成功保存，刷新主界面展示审核组件
        if st.session_state.get("status") == "interrupted":
            st.rerun()
    # 前端状态恢复
    st.markdown("-----")
    st.subheader("🔄 恢复历史进度")
    recover_id = st.text_input("输入 Thread_ID")
    if st.button("恢复审核进度", use_container_width=True):
        try:
            res = requests.get(f"{API_BASE}/status/{recover_id}")
            if res.status_code == 200:
                state_data = res.json()
                if state_data.get("status") == "interrupted":
                    st.session_state.thread_id = recover_id
                    st.session_state.status = "interrupted"
                    st.session_state.data = state_data.get("data", {})
                    st.success("进度恢复成功！")
                    st.rerun()
                else:
                    st.warning(f"该流程状态为: {state_data.get('status')}，无法恢复到审核环节。")
            else:
                st.error("未找到该 Thread ID 对应的记录。")
        except Exception as e:
            st.error(f"恢复失败: {e}")

# ================= 主面板：状态展示与人机交互 =================
if st.session_state.status =="interrupted":
    st.subheader("👀 人工审核环节")
    data = st.session_state.data

    col1,col2 = st.columns([2,1])
    with col1:
        st.markdown("**生成的标题：**")
        st.info(data.get("title","无"))
        st.markdown("**生成的正文：**")
        st.info(data.get("body","无"))
        st.markdown("**绘图：**")
        st.code(data.get("image_prompt","无"),language="markdown")
    with col2:
        st.markdown("**审核操作：**")
        if st.button("✅ 审核通过，继续生成",type="primary",use_container_width=True):
            # 🔥 改为 SSE 流式接收逻辑
            with st.status("正在生成配图并进行VLM审查...",expanded=True) as status:
                try:
                    res = requests.post(
                        f"{API_BASE}/resume/{st.session_state.thread_id}",
                        json={"approved":True},
                        stream=True,
                        timeout=120
                    )
                    if res.status_code != 200:
                        st.error(f"后端报错！状态码：{res.status_code}")
                    else:
                        final_data = None
                        log_placeholder = st.empty()  # 🔥 TODO 4: 用占位符实现多行日志流式更新
                        log_text = ""
                        # 逐行读取后端发来的进度
                        for line in res.iter_lines(decode_unicode=True):
                            if line and line.startswith("data:"):
                                # 🔥 修复：使用 [5:].strip() 去除前缀和多余空格
                                json_str = line[5:].strip()
                                try:
                                    event_data = json.loads(json_str)
                                    event_type = event_data.get("type")

                                    # 🔥 修复：明确区分日志和最终结果
                                    if event_type == "log":
                                        log_text += event_data.get("content", "") + "\n\n"
                                        log_placeholder.markdown(log_text)  # 原地更新日志
                                    elif event_type == "finished":
                                        final_data = event_data
                                    elif event_type == "error":
                                        st.error(event_data.get("message", ""))
                                except json.JSONDecodeError:
                                    st.write(json_str)

                        # 流结束后，处理最终数据
                        if final_data:
                            st.session_state.status = "finished"
                            # 🔥 注意：后端最终返回的数据直接就在 data 字段里
                            st.session_state.data = final_data.get("data", {})
                            status.update(label="工作流执行完毕！", state="complete", expanded=False)
                            time.sleep(2)
                            st.rerun()
                        else:
                            st.error("未收到最终结果数据")
                            status.update(label="执行出错", state="error")
                except Exception as e:
                    st.error(f"请求异常: {e}")
                    status.update(label="请求异常", state="error")

        # 🔥 核心修改：去掉 st.form，改用普通组件，避免表单冻结流式渲染
        st.markdown("**打回重写：**")
        feedback = st.text_area("请输入修改意见", placeholder="例如：标题不够吸引人，正文需要突出轻量化",
                                key="reject_feedback")

        if st.button("❌ 打回重写", use_container_width=True):
            if not feedback:
                st.warning("请输入修改意见后再打回重写。")
            else:
                with st.status("正在根据反馈重写文案...", expanded=True) as status:
                    try:
                        res = requests.post(
                            f"{API_BASE}/resume/{st.session_state.thread_id}",
                            json={"approved": False, "feedback": feedback},
                            stream=True,
                            timeout=120
                        )
                        if res.status_code != 200:
                            st.error(f"后端报错！状态码{res.status_code}")
                        else:
                            # 🔥 创建占位符，准备迎接逐字流式输出
                            title_placeholder = st.empty()
                            body_placeholder = st.empty()
                            full_title = ""
                            full_body = ""
                            for line in res.iter_lines(decode_unicode=True):
                                if line and line.startswith("data:"):
                                    json_str = line[5:].strip()

                                    try:
                                        chunk = json.loads(json_str)
                                        chunk_type = chunk.get("type")

                                        # 🔥 主面板实时渲染打字机效果
                                        if chunk_type == "title_chunk":
                                            full_title += chunk["content"]
                                            title_placeholder.markdown(full_title)

                                        elif chunk_type == "body_chunk":
                                            full_body += chunk["content"]
                                            body_placeholder.markdown(full_body)

                                        elif chunk_type == "finished":
                                            st.session_state.thread_id = chunk["thread_id"]
                                            st.session_state.status = chunk["status"]
                                            st.session_state.data = chunk["data"]

                                            status.update(
                                                label="✅ 文案重写完毕，请重新审核",
                                                state="complete",
                                                expanded=False
                                            )
                                            time.sleep(1.5)

                                        elif chunk_type == "error":
                                            st.error(chunk["message"])

                                    except json.JSONDecodeError:
                                        continue
                            if st.session_state.status == "interrupted":
                                st.rerun()
                    except Exception as e:
                        st.error(f"请求异常: {e}")


elif st.session_state.status =="finished":
    st.subheader("🎉 最终物料产出")
    data = st.session_state.data
    col1,col2 = st.columns(2)
    with col1:
        st.markdown("**最终标题:**")
        st.success(data.get("title", "无"))
        st.markdown("**最终正文:**")
        st.success(data.get("body", "无"))

    with col2:
        st.markdown("**最终配图:**")
        if data.get("image_url"):
            st.image(data["image_url"], caption="AI 生成配图")
        else:
            st.warning("未获取到图片")

    st.markdown("---")
    st.write(f"**审查状态:** {'通过' if data.get('review_passed') else '未通过/超时'}")
    st.write(f"**迭代次数:** {data.get('retry_count', 0)}")

    if st.button("🔄 开始新一轮生成"):
        st.session_state.status = "idle"
        st.session_state.thread_id = None
        st.rerun()










