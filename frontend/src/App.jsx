import { useState, useEffect } from 'react';
import { startWorkflow, resumeWorkflow, getHistory, rateMaterial, deleteMaterial } from './api';
import { Sparkles, Loader2, Cpu, Check, AlertCircle, Wand2, FileText, Tag, Copy, History, Trash2, Star, ArrowLeft } from 'lucide-react';

function App() {
  const [formData, setFormData] = useState({
    product_name: '', key_features: '', target_audience: '', platform: 'xhs'
  });

  const [isGenerating, setIsGenerating] = useState(false);
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [logs, setLogs] = useState([]);

  const [threadId, setThreadId] = useState(null);
  const [isInterrupted, setIsInterrupted] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [imageUrl, setImageUrl] = useState('');
  const [reviewPassed, setReviewPassed] = useState(false);
  const [currentScore, setCurrentScore] = useState(80);
  const [rateSuccess, setRateSuccess] = useState(false);

  const [activeTab, setActiveTab] = useState('workspace');
  const [historyList, setHistoryList] = useState([]);
  const [selectedHistory, setSelectedHistory] = useState(null);
    // 🔥 新增：分页状态
  const [historyPage, setHistoryPage] = useState(0);
  const itemsPerPage = 9; // 每页展示 6 个

  useEffect(() => {
    if (activeTab === 'history' && !selectedHistory) {
      getHistory().then(data => setHistoryList(data));
    }
  }, [activeTab, selectedHistory]);

  const handleChange = (e) => setFormData({ ...formData, [e.target.name]: e.target.value });

  const handleStart = async () => {
    if (!formData.product_name) return alert("请输入产品名称");
    setActiveTab('workspace');
    setIsGenerating(true);
    setTitle(''); setBody(''); setLogs([]); setImageUrl(''); setThreadId(null); setRateSuccess(false);
    setIsInterrupted(false);

    await startWorkflow(formData, {
      onTitle: (char) => setTitle(prev => prev + char),
      onBody: (char) => setBody(prev => prev + char),
      onLog: (msg) => setLogs(prev => [...prev, msg]),
      onFinished: (data) => handleFinished(data),
      onError: (msg) => { console.error("报错:", msg); setIsGenerating(false); alert(msg); },
      onClose: () => {}
    });
  };

  const handleFinished = (data) => {
    if (data.status === "interrupted") {
      setThreadId(data.thread_id);
      setIsInterrupted(true);
      if (data.data) {
        setTitle(data.data.title || "");
        setBody(data.data.body || "");
      }
    } else {
      setIsInterrupted(false);
      if (data.data) {
        setImageUrl(data.data.image_url || "");
        setReviewPassed(data.data.review_passed || false);
      }
    }
    setIsGenerating(false);
  };

  const handleApprove = async () => {
    setIsInterrupted(false);
    setIsGenerating(true);
    setLogs(prev => [...prev, '🚀 接收到审核通过指令，准备生成配图...']);
    await resumeWorkflow(threadId, { approved: true, feedback: '' }, {
      onLog: (msg) => setLogs(prev => [...prev, msg]),
      onFinished: (data) => handleFinished(data),
      onError: (msg) => { setIsGenerating(false); alert(msg); },
      onClose: () => {}
    });
  };

  const handleReject = async () => {
    if (!feedback) return alert("请输入修改意见");
    setIsInterrupted(false);
    setIsGenerating(true);
    setTitle(''); setBody('');
    setLogs(prev => [...prev, `❌ 收到打回重写指令，反馈：${feedback}`]);
    await resumeWorkflow(threadId, { approved: false, feedback }, {
      onTitle: (char) => setTitle(prev => prev + char),
      onBody: (char) => setBody(prev => prev + char),
      onLog: (msg) => setLogs(prev => [...prev, msg]),
      onFinished: (data) => handleFinished(data),
      onError: (msg) => { setIsGenerating(false); alert(msg); },
      onClose: () => {}
    });
  };

  const handleRate = async (id) => {
    await rateMaterial(id, currentScore);
    setRateSuccess(true);
  };

  const handleDelete = async (id) => {
    if (!window.confirm("确定删除该物料吗？")) return;
    await deleteMaterial(id);
    setHistoryList(prev => prev.filter(item => item.thread_id !== id));
    if (selectedHistory?.thread_id === id) setSelectedHistory(null);
    // 🔥 新增：如果当前页删空了，回退到上一页
    const totalRemainingPages = Math.ceil(newList.length / itemsPerPage);
    if (historyPage >= totalRemainingPages && historyPage > 0) {
      setHistoryPage(totalRemainingPages - 1);
    }
  };

  const tags = body.match(/#[^\s#]+/g) || [];

  const agentSteps = [
    { id: 'researcher', name: '需求分析', icon: Cpu, keywords: ['Researcher', '策略'] },
    { id: 'copywriter', name: '文案生成', icon: Wand2, keywords: ['Copywriter', '生成标题'] },
    { id: 'adapter', name: '风格适配', icon: Sparkles, keywords: ['Adapter', '风格'] },
    { id: 'visual', name: '视觉审查', icon: AlertCircle, keywords: ['VLM', '视觉', 'ImageGen'] }
  ];

  const getStepStatus = (stepId, index) => {
    if (!isGenerating && logs.length === 0) return 'idle';
    const step = agentSteps.find(s => s.id === stepId);
    const hasStarted = logs.some(log => step.keywords.some(kw => log.includes(kw)));
    const isDone = logs.some(log => log.includes('✅') && step.keywords.some(kw => log.includes(kw)));
    const currentStepIndex = agentSteps.findIndex(s => logs.some(log => s.keywords.some(kw => log.includes(kw)) && !log.includes('✅')));
    if (isDone) return 'done';
    if (hasStarted && (index === currentStepIndex || currentStepIndex === -1)) return 'active';
    if (hasStarted) return 'done';
    return 'pending';
  };

  return (
    <div className="min-h-screen flex flex-col bg-glow font-sans text-slate-200">
      {/* Header */}
      <header className="h-16 border-b border-slate-800 bg-slate-950/80 backdrop-blur-md flex items-center justify-between px-8 sticky top-0 z-50">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-teal-500/10 rounded-lg border border-teal-500/20"><Cpu className="text-teal-400" size={20} /></div>
          <div>
            <h1 className="text-base font-semibold text-white leading-none">营销物料生成台</h1>
            <p className="text-[11px] text-slate-500 mt-1">Multi-Agent Content Studio</p>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2 text-xs text-slate-400 font-medium bg-slate-800/80 border border-slate-700 px-3 py-1.5 rounded-full">
            <span className="w-1.5 h-1.5 bg-teal-400 rounded-full animate-pulse"></span>Agent Ready
          </div>
        </div>
      </header>

      <div className="flex-1 flex flex-col lg:flex-row overflow-hidden">
        {/* 侧边栏 */}
        <aside className="w-full lg:w-[340px] border-r border-slate-800 bg-slate-950/50 p-6 overflow-y-auto flex-shrink-0 backdrop-blur-sm">
          <div className="mb-6">
            <h2 className="text-sm font-semibold text-white">任务配置</h2>
            <p className="text-xs text-slate-500 mt-1">Configure your campaign</p>
          </div>
          <div className="space-y-5">
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-slate-300">产品名称</label>
              <p className="text-[11px] text-slate-500 mb-1">告诉 Agent 你要推广什么产品</p>
              <input type="text" name="product_name" value={formData.product_name} onChange={handleChange} placeholder="请输入产品名称" className="w-full px-3 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-500/20 placeholder:text-slate-600 text-white" />
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-slate-300">核心卖点</label>
              <p className="text-[11px] text-slate-500 mb-1">突出产品最重要的营销价值</p>
              <textarea name="key_features" rows="3" value={formData.key_features} onChange={handleChange} placeholder="请输入核心卖点..." className="w-full px-3 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-500/20 placeholder:text-slate-600 resize-none text-white" />
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-slate-300">目标受众</label>
              <p className="text-[11px] text-slate-500 mb-1">描述你的目标用户画像</p>
              <input type="text" name="target_audience" value={formData.target_audience} onChange={handleChange} placeholder="例如：学生党、上班族" className="w-full px-3 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-500/20 placeholder:text-slate-600 text-white" />
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-slate-300">投放渠道</label>
              <div className="relative">
                <select name="platform" value={formData.platform} onChange={handleChange} className="w-full appearance-none px-3 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-500/20 text-white">
                  <option value="general">通用风格</option>
                  <option value="xhs">小红书</option>
                  <option value="wechat">微信公众号</option>
                </select>
                <svg className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500 pointer-events-none" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7"></path></svg>
              </div>
            </div>
          </div>
          <button onClick={handleStart} disabled={isGenerating} className="w-full mt-8 bg-teal-500 text-slate-900 text-sm font-semibold py-3 rounded-lg flex items-center justify-center gap-2 hover:bg-teal-400 hover:shadow-lg hover:shadow-teal-500/20 hover:-translate-y-0.5 disabled:bg-slate-700 disabled:text-slate-400 disabled:translate-y-0 transition-all duration-300">
            {isGenerating ? <><Loader2 className="animate-spin" size={18} /> 正在启动 Agent...</> : <><Sparkles size={18} /> 启动 Agent 工作流</>}
          </button>
        </aside>

        {/* 主区域 */}
        <main className="flex-1 overflow-y-auto p-8 bg-grid">
          <div className="max-w-4xl mx-auto">

            <div className="flex items-center gap-4 mb-6 border-b border-slate-800">
              <button onClick={() => setActiveTab('workspace')} className={`pb-3 text-sm font-medium transition-colors ${activeTab === 'workspace' ? 'text-teal-400 border-b-2 border-teal-400' : 'text-slate-500 hover:text-slate-300'}`}>工作台</button>
              <button onClick={() => { setActiveTab('history'); setSelectedHistory(null); setHistoryPage(0);}} className={`pb-3 text-sm font-medium transition-colors flex items-center gap-1.5 ${activeTab === 'history' ? 'text-teal-400 border-b-2 border-teal-400' : 'text-slate-500 hover:text-slate-300'}`}><History size={14} /> 历史物料画廊</button>
            </div>

            {activeTab === 'workspace' && (
              <>
                {/* Agent Pipeline */}
                {(isGenerating || logs.length > 0) && (
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
                    {agentSteps.map((step, index) => {
                      const status = getStepStatus(step.id, index);
                      const Icon = step.icon;
                      return (
                        <div key={step.id} className={`p-3 rounded-xl border transition-all duration-300 backdrop-blur-sm ${status === 'active' ? 'bg-teal-500/10 border-teal-500/30 shadow-lg shadow-teal-500/10' : status === 'done' ? 'bg-slate-900/50 border-slate-700' : 'bg-slate-900/30 border-slate-800 opacity-50'}`}>
                          <div className="flex items-center gap-2 mb-1">
                            <div className={`p-1 rounded-md ${status === 'active' ? 'bg-teal-500 text-slate-900' : status === 'done' ? 'bg-teal-500/20 text-teal-400' : 'bg-slate-800 text-slate-500'}`}>
                              {status === 'done' ? <Check size={14} /> : <Icon size={14} />}
                            </div>
                            <span className={`text-xs font-medium ${status === 'pending' ? 'text-slate-500' : 'text-slate-200'}`}>{step.name}</span>
                          </div>
                          <p className={`text-[11px] pl-7 ${status === 'active' ? 'text-teal-400' : 'text-slate-500'}`}>
                            {status === 'active' ? '正在处理...' : status === 'done' ? '已完成' : '等待中'}
                          </p>
                        </div>
                      );
                    })}
                  </div>
                )}

                {/* 日志状态条 */}
                {logs.length > 0 && (
                  <div className="mb-6 px-4 py-3 bg-slate-900/80 rounded-xl border border-slate-800 flex items-center gap-2 text-xs text-slate-400 font-mono backdrop-blur-sm">
                    <span className="text-teal-400 select-none">▸</span>
                    <span className="truncate flex-1">{logs[logs.length - 1]}</span>
                    {isGenerating && <Loader2 className="animate-spin text-teal-400 ml-auto flex-shrink-0" size={14} />}
                  </div>
                )}

                {/* 图文合并卡片 */}
                {(title || body || imageUrl) && (
                  <div className="bg-slate-900/60 backdrop-blur-md rounded-xl border border-slate-800 shadow-xl p-6 transition-all mb-6">
                    {title && (
                      <h2 className="text-xl font-semibold text-white mb-6 tracking-tight">
                        {title}{isGenerating && <span className="inline-block w-0.5 h-5 bg-teal-400 ml-1 animate-pulse align-middle"></span>}
                      </h2>
                    )}
                    <div className="flex flex-col md:flex-row gap-6 items-start">
                      <div className="md:w-3/5 flex-1">
                        {body && <div className="text-slate-300 text-[15px] leading-loose whitespace-pre-wrap">{body}</div>}
                        {tags.length > 0 && (
                          <div className="mt-6 pt-4 border-t border-slate-800 flex items-center gap-2 flex-wrap">
                            <Tag size={14} className="text-slate-500" />
                            {tags.map((tag, i) => <span key={i} className="px-2 py-0.5 bg-slate-800 text-slate-400 text-xs rounded-md border border-slate-700">{tag}</span>)}
                          </div>
                        )}
                      </div>
                      {imageUrl && (
                        <div className="md:w-2/5 w-full flex-shrink-0">
                          <img src={`http://localhost:8000${imageUrl}`} alt="AI生成配图" className="rounded-lg w-full h-auto max-h-96 object-cover bg-slate-950 border border-slate-800" />
                          <div className={`mt-2 text-center text-xs px-2 py-1 rounded-full font-medium ${reviewPassed ? 'bg-teal-500/10 text-teal-400 border border-teal-500/20' : 'bg-red-500/10 text-red-400 border border-red-500/20'}`}>
                            {reviewPassed ? '✓ 视觉审查通过' : '✗ 视觉审查未通过'}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* 人工审核面板 */}
                {isInterrupted && (
                  <div className="bg-teal-500/5 rounded-xl border border-teal-500/30 shadow-lg p-6 mb-6 backdrop-blur-sm">
                    <div className="flex items-center gap-2 text-sm font-medium text-teal-400 mb-4"><AlertCircle size={16} />人工审核环节</div>
                    <div className="mb-4">
                      <label className="text-xs font-medium text-slate-400 mb-2 block">如果不满意，请输入修改意见打回重写：</label>
                      <textarea value={feedback} onChange={(e) => setFeedback(e.target.value)} rows="2" placeholder="例如：标题不够吸引人" className="w-full px-3 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-500/20 resize-none text-white placeholder:text-slate-600" />
                    </div>
                    <div className="flex gap-3">
                      <button onClick={handleApprove} className="flex-1 bg-teal-500 text-slate-900 text-sm font-semibold py-2.5 rounded-lg hover:bg-teal-400 transition-colors flex items-center justify-center gap-2"><Check size={16} /> 审核通过，生成配图</button>
                      <button onClick={handleReject} className="flex-1 bg-transparent border border-red-500/30 text-red-400 text-sm font-medium py-2.5 rounded-lg hover:bg-red-500/10 transition-colors flex items-center justify-center gap-2"><AlertCircle size={16} /> 打回重写</button>
                    </div>
                  </div>
                )}

                {/* 打分面板与重置按钮 */}
                {!isInterrupted && imageUrl && !isGenerating && (
                  <>
                    <div className="bg-slate-900/60 backdrop-blur-md rounded-xl border border-slate-800 shadow-xl p-6 mb-6">
                      <div className="flex items-center gap-2 mb-4 text-sm font-medium text-slate-200"><Star size={16} className="text-yellow-400" /> 请对本套物料进行评分</div>
                      {!rateSuccess ? (
                        <div className="flex items-center gap-4">
                          <input type="range" min="0" max="100" value={currentScore} onChange={(e) => setCurrentScore(e.target.value)} className="flex-1 accent-teal-400" />
                          <span className="text-lg font-bold text-teal-400 w-12 text-right">{currentScore}</span>
                          <button onClick={() => handleRate(threadId)} className="bg-slate-100 text-slate-900 text-sm px-4 py-2 rounded-lg hover:bg-white">提交评分</button>
                        </div>
                      ) : (
                        <div className="flex items-center justify-between bg-teal-500/10 text-teal-400 p-3 rounded-lg text-sm border border-teal-500/20">
                          <span>✓ 评分提交成功！{currentScore >= 90 ? '该优质物料已自动进入范例库。' : '感谢您的评价。'}</span>
                          <button onClick={() => { setRateSuccess(false); setActiveTab('history'); }} className="text-teal-300 font-medium hover:underline">去画廊查看</button>
                        </div>
                      )}
                      <p className="text-xs text-slate-500 mt-2">评分 ≥90 分的物料将自动沉淀至 RAG 知识库供后续参考</p>
                    </div>
                    <div className="text-center">
                      <button onClick={() => { setTitle(''); setBody(''); setLogs([]); setImageUrl(''); setThreadId(null); setIsInterrupted(false); setRateSuccess(false); setCurrentScore(80); document.querySelector('input[name="product_name"]')?.focus(); }} className="bg-slate-800 text-slate-300 text-sm font-medium px-6 py-2.5 rounded-lg hover:bg-slate-700 transition-colors border border-slate-700">重新配置任务</button>
                    </div>
                  </>
                )}

                {/* 空状态 */}
                {!title && !body && !imageUrl && logs.length === 0 && (
                  <div className="bg-slate-900/30 backdrop-blur-sm rounded-xl border border-dashed border-slate-700 p-16 flex flex-col items-center justify-center text-center">
                    <div className="p-4 bg-slate-900 rounded-full border border-slate-800 mb-4"><Sparkles size={28} className="text-teal-400" /></div>
                    <h3 className="text-lg font-medium text-slate-200 mb-2">Ready to create</h3>
                    <p className="text-sm text-slate-500 max-w-xs">配置你的营销任务，Agent 将自动完成内容策划、文案生成和优化</p>
                  </div>
                )}
              </>
            )}

            {/* 历史画廊 */}
            {activeTab === 'history' && (
              <>
                {selectedHistory ? (
                  <div>
                    <button onClick={() => setSelectedHistory(null)} className="mb-4 text-sm text-slate-400 hover:text-teal-400 flex items-center gap-1"><ArrowLeft size={14} /> 返回列表</button>
                    <div className="bg-slate-900/60 backdrop-blur-md rounded-xl border border-slate-800 shadow-xl p-6">
                      <div className="flex justify-between items-start mb-4">
                        <h2 className="text-xl font-semibold text-white">{selectedHistory.title}</h2>
                        <button onClick={() => handleDelete(selectedHistory.thread_id)} className="text-red-400/70 hover:text-red-400 p-1"><Trash2 size={16} /></button>
                      </div>
                      <div className="flex flex-col md:flex-row gap-6">
                        {selectedHistory.image_url && <img src={`http://localhost:8000${selectedHistory.image_url}`} className="rounded-lg w-full md:w-1/2 object-contain bg-slate-950 border border-slate-800" />}
                        <div className="flex-1">
                          <div className="text-slate-300 text-[15px] leading-loose whitespace-pre-wrap">{selectedHistory.body}</div>
                          <div className="mt-6 pt-4 border-t border-slate-800 flex items-center gap-2 text-slate-400">
                            <Star size={14} className="text-yellow-400" /> 当前评分: <span className="font-bold text-teal-400">{selectedHistory.quality_score || '未评'}</span>
                          </div>
                          <div className="mt-4 flex items-center gap-2">
                            <input type="range" min="0" max="100" defaultValue={selectedHistory.quality_score || 80} onChange={(e) => setSelectedHistory({...selectedHistory, quality_score: e.target.value})} className="flex-1 accent-teal-400" />
                            <button onClick={() => handleRate(selectedHistory.thread_id)} className="bg-slate-100 text-slate-900 text-sm px-4 py-2 rounded-lg">更新评分</button>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="pb-24"> {/* 🔥 修改：增加底部留白，防止最后一张卡片被挡住 */}
                    <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                      {historyList.length === 0 && <p className="text-slate-500 text-sm col-span-full text-center py-10">暂无历史物料</p>}

                      {/* 渲染当前页的数据 */}
                      {historyList.slice(historyPage * itemsPerPage, (historyPage + 1) * itemsPerPage).map(item => (
                        <div key={item.thread_id} onClick={() => setSelectedHistory(item)} className="bg-slate-900/60 backdrop-blur-sm rounded-xl border border-slate-800 overflow-hidden cursor-pointer hover:border-teal-500/30 hover:shadow-lg hover:shadow-teal-500/5 transition-all group">
                          <div className="aspect-square bg-slate-950 overflow-hidden">
                            {item.image_url && <img src={`http://localhost:8000${item.image_url}`} className="w-full h-full object-cover group-hover:scale-105 transition-transform opacity-80 group-hover:opacity-100" />}
                          </div>
                          <div className="p-3 border-t border-slate-800">
                            <h4 className="text-sm font-medium text-slate-200 truncate">{item.title}</h4>
                            <div className="flex items-center justify-between mt-2">
                              <span className="text-xs text-slate-500 flex items-center gap-1"><Star size={12} className="text-yellow-400" /> {item.quality_score || '未评'}</span>
                              <span className="text-xs text-slate-500">{item.retry_count || 0} 次迭代</span>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>

                    {/* 🔥 修改：使用 fixed 绝对固定在底部，并避开左侧 340px 的侧边栏 */}
                    {historyList.length > itemsPerPage && (
                      <div className="fixed bottom-0 left-0 lg:left-[340px] right-0 z-40 py-4 bg-slate-950/80 backdrop-blur-md border-t border-slate-800 flex justify-center items-center gap-4 text-slate-400 text-sm">
                        <button
                          onClick={() => setHistoryPage(prev => Math.max(0, prev - 1))}
                          disabled={historyPage === 0}
                          className="px-4 py-1.5 rounded-md bg-slate-800 border border-slate-700 hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                        >
                          上一页
                        </button>
                        <span className="text-slate-300">
                          第 <span className="font-bold text-teal-400">{historyPage + 1}</span> / {Math.ceil(historyList.length / itemsPerPage)} 页
                        </span>
                        <button
                          onClick={() => setHistoryPage(prev => Math.min(Math.ceil(historyList.length / itemsPerPage) - 1, prev + 1))}
                          disabled={historyPage >= Math.ceil(historyList.length / itemsPerPage) - 1}
                          className="px-4 py-1.5 rounded-md bg-slate-800 border border-slate-700 hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                        >
                          下一页
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}

export default App;