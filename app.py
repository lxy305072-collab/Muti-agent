import streamlit as st
import requests
import json
import time

st.set_page_config(page_title="营销物料生成系统", page_icon="📦", layout="wide")
st.title("📦 营销物料自动化生成平台")

API_BASE = "http://localhost:8000/api"
BACKEND_BASE = "http://localhost:8000"
# ================= 初始化 Session State =================
if "thread_id" not in st.session_state:
    st.session_state.thread_id = None
# idle: 初始状态, streaming: 正在生成文案, interrupted: 人工审核中, finishing: 正在输出最终物料, finished: 全部完成
if "status" not in st.session_state:
    st.session_state.status = "idle"
if "data" not in st.session_state:
    st.session_state.data = {}
if "streaming_thread_id" not in st.session_state:
    st.session_state.streaming_thread_id = None
# 🔥 必须加上这段：初始化历史画廊的页码
if "history_page" not in st.session_state:
    st.session_state.history_page = 0

# ================= 侧边栏：产品信息输入 =================
with st.sidebar:
    st.header("🚀 产品信息配置")
    product_name = st.text_input("产品名称", placeholder="请输入产品名称")
    key_features = st.text_area("核心卖点", placeholder="请输入核心卖点")
    target_audience = st.text_input("目标受众", placeholder="请输入目标受众")
    # 🔥 新增：投放渠道选择下拉框
    platform_options = {
        "通用风格": "general",
        "小红书 (Emoji/种草)": "xhs",
        "微信公众号 (深度/专业)": "wechat"
    }
    selected_platform = st.selectbox("投放渠道", list(platform_options.keys()))
    platform = platform_options[selected_platform]  # 获取实际传给后端的值

    # 🔥 判断当前是否正在生成中
    is_generating = st.session_state.status in ["streaming", "finishing", "rejecting"]

    # 🔥 替换原来的按钮，加上 disabled=is_generating
    if st.button("开始生成", type="primary", width='stretch', disabled=is_generating):
        st.session_state.status = "streaming"
        st.session_state.streaming_thread_id = None
        st.rerun()


    st.markdown("-----")
    st.subheader("🔄 恢复历史进度")
    recover_id = st.text_input("输入 Thread_ID")
    if st.button("恢复审核进度", width='stretch'):
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
# 🔥 新增：将当前的 Thread_ID 显示在页面上，方便用户复制记录
st.caption(f"当前进度 ID: `{st.session_state.thread_id}`")

# 使用 Tabs 划分工作台和历史画廊
tab_workspace,tab_history = st.tabs(["🛠️ 工作台", "🖼️ 历史物料画廊"])

with tab_history:
    st.subheader("历史物料画廊")
    if st.button("🔄 刷新画廊", width='content'):
        st.session_state.history_page = 0
        st.rerun()

    try:
        res = requests.get(f"{API_BASE}/history", timeout=5)
        if res.status_code == 200:
            history_data = res.json()
            if not history_data:
                st.info("暂无历史物料，快去工作台生成第一个吧！")
            else:
                # 🔥 分页配置：每页6个，3列布局
                items_per_page = 6
                total_items = len(history_data)
                total_pages = max(1, (total_items + items_per_page - 1) // items_per_page)

                # 防止页码越界（删除物料后数据变少时自动回到第1页）
                if st.session_state.history_page >= total_pages:
                    st.session_state.history_page = 0

                start_idx = st.session_state.history_page * items_per_page
                end_idx = start_idx + items_per_page
                page_items = history_data[start_idx:end_idx]

                # 🔥 渲染当前页的6个物料（每行3个，共2行）
                cols = st.columns(3)
                for idx, item in enumerate(page_items):
                    # 过滤掉没有图片的物料
                    if not item.get("image_url"):
                        continue
                    with cols[idx % 3]:
                        with st.container(border=True):
                            img_url = item.get("image_url")
                            # 如果是相对路径，拼接上后端地址
                            if img_url and img_url.startswith("/"):
                                img_url = f"http://localhost:8000{img_url}"
                            st.image(img_url, caption=item.get("title", "无标题"), width='stretch')
                            st.markdown(f"**{item['title']}**")
                            score = item.get('quality_score', 0)
                            score_emoji = "🌟" if score >= 90 else ("⭐" if score > 0 else "🆕")
                            st.caption(
                                f"{score_emoji} 评分: {score} | 迭代: {item.get('retry_count', 0)} | ID: {item.get('thread_id', '未知')[:8]}...")

                            btn_col1, btn_col2 = st.columns(2)
                            # 🔥 key 使用全局索引 start_idx+idx 确保跨页唯一
                            if btn_col1.button("👁️ 详情", key=f"view_{start_idx + idx}", width='stretch'):
                                st.session_state.selected_history = item
                                st.rerun()

                            if btn_col2.button("🗑️ 删除", key=f"del_{start_idx + idx}", width='stretch'):
                                try:
                                    del_res = requests.delete(f"{API_BASE}/history/{item.get('thread_id')}")
                                    if del_res.status_code == 200:
                                        st.success("删除成功！")
                                        time.sleep(1)
                                        st.rerun()
                                except Exception as e:
                                    st.error(f"网络异常:{e}")

                # 🔥 分页导航栏
                st.markdown("---")
                nav_col1, nav_col2, nav_col3 = st.columns([1, 2, 1])

                with nav_col1:
                    if st.button("⬅️ 上一页", disabled=(st.session_state.history_page == 0),
                                 use_container_width=True):
                        st.session_state.history_page -= 1
                        st.rerun()

                with nav_col2:
                    st.markdown(
                        f"<div style='text-align:center; padding-top:8px; color:gray;'>"
                        f"第 <b>{st.session_state.history_page + 1}</b> / {total_pages} 页 "
                        f"（共 {total_items} 条物料）"
                        f"</div>",
                        unsafe_allow_html=True
                    )

                with nav_col3:
                    if st.button("下一页 ➡️",
                                 disabled=(st.session_state.history_page >= total_pages - 1),
                                 use_container_width=True):
                        st.session_state.history_page += 1
                        st.rerun()

        else:
            st.error("获取历史记录失败，请检查后端服务。")
    except requests.exceptions.RequestException:
        st.warning("后端服务未连接，无法获取历史记录。")

    # 🔥 保留原有的详情展示区域（不变）
    if "selected_history" in st.session_state and st.session_state.selected_history:
        item = st.session_state.selected_history
        st.markdown("---")
        st.subheader("📄 物料详情")

        if st.button("❌ 关闭详情"):
            st.session_state.selected_history = None
            st.rerun()

        score = item.get('quality_score', 0)
        score_emoji = "🌟" if score >= 90 else ("⭐" if score > 0 else "🆕")

        meta_col1, meta_col2, meta_col3 = st.columns(3)
        meta_col1.markdown("**物料 ID:**")
        meta_col1.code(item.get("thread_id", "未知"), language=None)
        meta_col2.metric("综合评分", f"{score_emoji} {score} 分")
        meta_col3.metric("迭代次数", f"{item.get('retry_count', 0)} 次")

        st.markdown("---")

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**标题:**")
            st.success(item.get("title", "无"))
            st.markdown("**正文:**")
            st.success(item.get("body", "无"))
            st.markdown("**绘图提示词:**")
            st.code(item.get("image_prompt", "无"), language="markdown")

        with col2:
            st.markdown("**配图:**")
            if item.get("image_url"):
                img_url = item.get("image_url")
                if img_url and img_url.startswith("/"):
                    img_url = f"http://localhost:8000{img_url}"
                st.image(img_url, caption="AI 生成配图", width='stretch')
            else:
                st.warning("该物料未生成图片")

            st.markdown("---")
            current_score = item.get('quality_score', 0)

            # 🔥 核心：Form 只负责收集分数和捕获点击事件
            with st.form(f"rate_history_form_{item.get('thread_id')}"):
                new_score = st.slider("给该物料打分 (≥90分将自动进入标准范例库)", 0, 100, current_score)
                submitted = st.form_submit_button("提交评分", type="primary")

            # 🔥 核心：在 Form 外部处理网络请求
            if submitted:
                if new_score != current_score:
                    try:
                        with st.spinner('正在提交评分...'):
                            res = requests.post(f"{API_BASE}/rate",
                                                json={"thread_id": item.get('thread_id'), "score": new_score},
                                                timeout=30)

                            # 🔥 终极防御
                            res_data = {}
                            if res.status_code == 200 and 'application/json' in res.headers.get('Content-Type', ''):
                                try:
                                    res_data = res.json()
                                except:
                                    pass

                            if res_data.get("status") == "success":
                                st.success(res_data.get("msg", "评分成功！"))
                                st.session_state.selected_history['quality_score'] = new_score
                                # 🔥 优化：使用 st.session_state 标记成功，在 form 外部执行 rerun
                                st.session_state.rate_success_hist = True
                            else:
                                err_msg = res_data.get('msg', f"服务器返回异常 (状态码: {res.status_code})")
                                st.error(f"打分失败: {err_msg}")
                    except Exception as e:
                        st.error(f"网络异常: {e}")
                else:
                    st.info("分数未变化")

            # 🔥 在 form 外部检查并执行页面重置与刷新
            if st.session_state.get("rate_success_hist"):
                st.session_state.pop("rate_success_hist", None)
                time.sleep(1.5)
                st.rerun()

with tab_workspace:
    # 1. 初始生成文案阶段：主界面流式输出
    if st.session_state.status == "streaming":
        st.subheader("✍️ 正在生成营销文案...")

        title_placeholder = st.empty()
        body_placeholder = st.empty()
        status_placeholder = st.empty()

        full_title = ""
        full_body = ""

        try:
            with st.status("正在连接大模型...", expanded=True) as status:
                if not st.session_state.streaming_thread_id:
                    payload = {
                        "product_name": product_name,
                        "key_features": key_features,
                        "target_audience": target_audience,
                        "platform": platform  # 🔥 新增：将渠道传入后端
                    }
                    res = requests.post(f"{API_BASE}/start", json=payload, stream=True)

                    if res.status_code != 200:
                        try:
                            error_data = res.json()
                            error_msg = error_data.get("detail", "未知服务端错误")
                        except:
                            error_msg = res.text

                        st.error(f"🚫 请求失败 (状态码: {res.status_code}) - {error_msg}")
                        st.session_state.status = "idle"
                        st.stop()  # 错误发生时停止执行
                status.update(label="📡 已连接，正在接收数据...", expanded=False)

            for line in res.iter_lines(decode_unicode=True):
                if line and line.startswith("data: "):
                    json_str = line[5:].strip()
                    try:
                        chunk = json.loads(json_str)
                        chunk_type = chunk.get("type")

                        if chunk_type == "title_chunk":
                            full_title += chunk["content"]
                            title_placeholder.markdown(f"**生成的标题：**\n\n{full_title}")

                        elif chunk_type == "body_chunk":
                            full_body += chunk["content"]
                            body_placeholder.markdown(f"**生成的正文：**\n\n{full_body}")

                        elif chunk_type == "log":
                            status_placeholder.info(chunk.get("content", ""))

                        elif chunk_type == "finished":
                            st.session_state.thread_id = chunk["thread_id"]
                            st.session_state.status = chunk["status"]
                            st.session_state.data = chunk["data"]
                            st.session_state.streaming_thread_id = None
                            time.sleep(0.3)
                            st.rerun()

                        elif chunk_type == "error":
                            st.error(chunk.get("message", "未知错误"))
                            st.session_state.status = "idle"
                            st.rerun()

                    except json.JSONDecodeError:
                        continue

        except requests.exceptions.RequestException as e:
            st.error(f"网络连接异常: {e}")
            st.session_state.status = "idle"


    # 2. 人工审核阶段
    elif st.session_state.status == "interrupted":
        st.subheader("👀 人工审核环节")
        data = st.session_state.data



        col1, col2 = st.columns([2, 1])
        with col1:
            st.markdown("**生成的标题：**")
            st.info(data.get("title", "无"))
            st.markdown("**生成的正文：**")
            st.info(data.get("body", "无"))
            st.markdown("**绘图提示词：**")
            st.code(data.get("image_prompt", "无"), language="markdown")

        with col2:
            st.markdown("**审核操作：**")

            # 🔥 审核通过：进入最终流式输出阶段
            if st.button("✅ 审核通过，继续生成", type="primary", width='stretch'):
                st.session_state.status = "finishing"
                st.rerun()

            st.markdown("**打回重写：**")
            feedback = st.text_area("请输入修改意见", placeholder="例如：标题不够吸引人，正文需要突出轻量化",
                                    key="reject_feedback")

            if st.button("❌ 打回重写", width='stretch'):
                if not feedback:
                    st.warning("请输入修改意见后再打回重写。")
                else:
                    st.session_state.status = "rejecting"
                    st.session_state.feedback = feedback
                    st.rerun()


    # 3. 🔥 新增：最终物料流式输出阶段 (finishing)
    elif st.session_state.status == "finishing":
        st.subheader("🚀 正在生成最终物料...")

        # 准备占位符
        title_placeholder = st.empty()
        body_placeholder = st.empty()
        image_placeholder = st.empty()
        log_placeholder = st.empty()

        full_title = ""
        full_body = ""
        final_data = None

        try:
            with st.status("正在生成配图并进行VLM审查...", expanded=True) as status:
                res = requests.post(
                    f"{API_BASE}/resume/{st.session_state.thread_id}",
                    json={"approved": True},
                    stream=True,
                    timeout=120
                )
                if res.status_code != 200:
                    st.error(f"后端报错！状态码：{res.status_code}")
                    st.session_state.status = "interrupted"
                    st.rerun()
                else:
                    for line in res.iter_lines(decode_unicode=True):
                        if line and line.startswith("data:"):
                            json_str = line[5:].strip()
                            try:
                                event_data = json.loads(json_str)
                                event_type = event_data.get("type")

                                # 如果后端有推送文案 chunk，就实时渲染（可选，取决于你的 copywriter 是否在此阶段被触发）
                                if event_type == "title_chunk":
                                    full_title += event_data["content"]
                                    title_placeholder.markdown(f"**最终标题:**\n\n{full_title}")
                                elif event_type == "body_chunk":
                                    full_body += event_data["content"]
                                    body_placeholder.markdown(f"**最终正文:**\n\n{full_body}")
                                elif event_type == "log":
                                    log_placeholder.info(event_data.get("content", ""))
                                elif event_type == "finished":
                                    final_data = event_data
                                elif event_type == "error":
                                    st.error(event_data.get("message", ""))
                            except json.JSONDecodeError:
                                continue

                    if final_data:
                        st.session_state.status = "finished"
                        st.session_state.thread_id = final_data.get("thread_id",st.session_state.thread_id)  # 🔥 保存 thread_id
                        st.session_state.data = final_data.get("data", {})
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error("未收到最终结果数据")
                        st.session_state.status = "interrupted"
                        st.rerun()
        except Exception as e:
            st.error(f"请求异常: {e}")
            st.session_state.status = "interrupted"
            st.rerun()


    # 4. 🔥 新增：打回重写流式输出阶段
    elif st.session_state.status == "rejecting":
        st.subheader("✍️ 正在根据反馈重写文案...")
        title_placeholder = st.empty()
        body_placeholder = st.empty()
        log_placeholder = st.empty()
        full_title = ""
        full_body = ""

        try:
            res = requests.post(
                f"{API_BASE}/resume/{st.session_state.thread_id}",
                json={"approved": False, "feedback": st.session_state.feedback},
                stream=True,
                timeout=120
            )
            if res.status_code != 200:
                st.error(f"后端报错！状态码{res.status_code}")
                st.session_state.status = "interrupted"
                st.rerun()
            else:
                for line in res.iter_lines(decode_unicode=True):
                    if line and line.startswith("data:"):
                        json_str = line[5:].strip()
                        try:
                            chunk = json.loads(json_str)
                            chunk_type = chunk.get("type")

                            if chunk_type == "title_chunk":
                                full_title += chunk["content"]
                                title_placeholder.markdown(f"**重写的标题：**\n\n{full_title}")
                            elif chunk_type == "body_chunk":
                                full_body += chunk["content"]
                                body_placeholder.markdown(f"**重写的正文：**\n\n{full_body}")
                            elif chunk_type == "log":
                                log_placeholder.info(chunk.get("content", ""))

                            elif chunk_type == "finished":
                                st.session_state.thread_id = chunk["thread_id"]
                                st.session_state.status = chunk["status"]
                                st.session_state.data = chunk["data"]
                                time.sleep(1.5)
                                st.rerun()

                            elif chunk_type == "error":
                                st.error(chunk["message"])

                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            st.error(f"请求异常: {e}")
            st.session_state.status = "interrupted"
            st.rerun()


    # 5. 最终完成展示阶段
    elif st.session_state.status == "finished":
        st.subheader("🎉 最终物料产出")
        data = st.session_state.data
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**最终标题:**")
            st.success(data.get("title", "无"))
            st.markdown("**最终正文:**")
            st.success(data.get("body", "无"))

        with col2:
            st.markdown("**最终配图:**")
            if data.get("image_url"):
                img_url = data.get("image_url")
                if img_url and img_url.startswith("/"):
                    img_url = f"http://localhost:8000{img_url}"
                st.image(img_url, caption="AI 生成配图")
            else:
                st.warning("未获取到图片")

        st.markdown("---")
        st.write(f"**审查状态:** {'通过' if data.get('review_passed') else '未通过/超时'}")
        st.write(f"**迭代次数:** {data.get('retry_count', 0)}")

        st.markdown("### 📊 请对本次物料进行评分")
        current_score = data.get('quality_score', 0)

        # 🔥 核心：Form 只负责收集分数和捕获点击事件
        with st.form("rate_final_form"):
            new_score = st.slider("滑块打分 (≥90分将自动沉淀进RAG供后续参考)", 0, 100, 80, key="final_slider")
            submitted = st.form_submit_button("✅ 提交评分并完成", type="primary")

        # 🔥 核心：在 Form 外部处理网络请求，彻底避免流中断导致 NoneType
        if submitted:
            if not st.session_state.thread_id:
                st.error("系统异常：找不到当前物料的 ID，请尝试在历史记录中打分。")
            else:
                try:
                    with st.spinner('正在提交评分...'):
                        res = requests.post(f"{API_BASE}/rate",
                                            json={"thread_id": st.session_state.thread_id, "score": new_score},
                                            timeout=30)

                        # 🔥 终极防御：只解析合法的 JSON 响应
                        res_data = {}
                        if res.status_code == 200 and 'application/json' in res.headers.get('Content-Type', ''):
                            try:
                                res_data = res.json()
                            except:
                                pass

                        if isinstance(res_data, dict) and res_data.get("status") == "success":
                            st.success(res_data.get("msg", "评分成功！"))
                            # 🔥 优化：使用 st.session_state 标记成功，在 form 外部执行 rerun
                            st.session_state.rate_success = True
                        else:
                            err_msg = res_data.get('msg', f"服务器返回异常 (状态码: {res.status_code})")
                            st.error(f"打分失败: {err_msg}")
                except Exception as e:
                    st.error(f"网络异常: {e}")

        # 🔥 在 form 外部检查并执行页面重置与刷新
        if st.session_state.get("rate_success"):
            st.session_state.pop("rate_success", None)
            st.session_state.status = "idle"
            st.session_state.thread_id = None
            time.sleep(1.5)
            st.rerun()

        if st.button("🔄 跳过打分，开始新一轮生成"):
            st.session_state.status = "idle"
            st.session_state.thread_id = None
            st.rerun()

    # 默认 idle 状态的提示
    elif st.session_state.status == "idle":
        st.info("👈 请在左侧侧边栏填写产品信息，点击【开始生成】启动工作流。")