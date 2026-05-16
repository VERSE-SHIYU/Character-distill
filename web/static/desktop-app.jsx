// desktop-app.jsx — 桌面全屏角色模拟器
// 功能：头像上传 / localStorage 持久化 / 别名合并 / 大文件 / 自动摘要 / 用户身份 / 多格式

const AVATAR_COLORS = ['#C9D8F5', '#E8D87A', '#7EC8C8', '#A8D878'];
function hashColor(name) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = ((h << 5) - h + name.charCodeAt(i)) | 0;
  return AVATAR_COLORS[Math.abs(h) % AVATAR_COLORS.length];
}

// ─── localStorage ─────────────────────────────────────────────
const STORAGE_KEY = 'char_sim_v2';
function loadState() {
  try { const r = localStorage.getItem(STORAGE_KEY); return r ? JSON.parse(r) : null; }
  catch (e) { return null; }
}
function saveState(s) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(s)); } catch (e) {}
}

// ─── 别名合并 ─────────────────────────────────────────────────
function isSamePerson(a, b) {
  if (!a || !b) return false;
  const la = a.replace(/\s/g, ''), lb = b.replace(/\s/g, '');
  if (la === lb) return true;
  if (la.includes(lb) || lb.includes(la)) return true;
  if (la.length === 2 && lb.length === 3 && lb.endsWith(la)) return true;
  if (lb.length === 2 && la.length === 3 && la.endsWith(lb)) return true;
  return false;
}
function mergeCharacters(chars) {
  if (!chars || !chars.length) return [];
  const merged = [], used = new Set();
  for (let i = 0; i < chars.length; i++) {
    if (used.has(i)) continue;
    const group = [chars[i]]; used.add(i);
    for (let j = i + 1; j < chars.length; j++) {
      if (used.has(j)) continue;
      if (isSamePerson(chars[i].name, chars[j].name)) { group.push(chars[j]); used.add(j); }
    }
    group.sort((a, b) => (b.name || '').length - (a.name || '').length);
    const p = { ...group[0] };
    if (group.length > 1) p.aliases = group.slice(1).map(g => g.name);
    merged.push(p);
  }
  return merged;
}

// ─── 对话自动摘要 ─────────────────────────────────────────────
const SUMMARY_THRESHOLD = 20;
function summarizeMessages(msgs) {
  if (msgs.length <= SUMMARY_THRESHOLD) return { summary: null, recent: msgs };
  const oldMsgs = msgs.slice(0, msgs.length - 10);
  const recentMsgs = msgs.slice(msgs.length - 10);
  const topics = oldMsgs
    .filter(m => m.role === 'user')
    .map(m => m.text.slice(0, 30))
    .slice(-5);
  const summary = `[已折叠 ${oldMsgs.length} 条消息] 话题包括：${topics.join('、') || '闲聊'}`;
  return { summary, recent: recentMsgs };
}

// ─── fetch 超时 ───────────────────────────────────────────────
function fetchT(url, opts, ms = 180000) {
  const c = new AbortController();
  const t = setTimeout(() => c.abort(), ms);
  return fetch(url, { ...opts, signal: c.signal }).finally(() => clearTimeout(t));
}

// ─── 主组件 ───────────────────────────────────────────────────
function DesktopApp() {
  const saved = React.useMemo(() => loadState(), []);

  const [text, setText] = React.useState(saved?.text || '');
  const [charName, setCharName] = React.useState(saved?.charName || '');
  const [userRole, setUserRole] = React.useState(saved?.userRole || '');
  const [loading, setLoading] = React.useState(false);
  const [loadingMsg, setLoadingMsg] = React.useState('');
  const [error, setError] = React.useState('');
  const [card, setCard] = React.useState(saved?.card || null);
  const [sessionId, setSessionId] = React.useState(saved?.sessionId || '');
  const [messages, setMessages] = React.useState(saved?.messages || []);
  const [chatInput, setChatInput] = React.useState('');
  const [sending, setSending] = React.useState(false);
  const [dragover, setDragover] = React.useState(false);
  const [textSummary, setTextSummary] = React.useState(saved?.textSummary || '');
  const [textLength, setTextLength] = React.useState(saved?.textLength || 0);
  const [history, setHistory] = React.useState(saved?.history || []);
  const [avatar, setAvatar] = React.useState(saved?.avatar || null);
  const [summaryText, setSummaryText] = React.useState(null);

  const messagesEndRef = React.useRef(null);
  const fileRef = React.useRef(null);
  const avatarRef = React.useRef(null);

  // 持久化
  React.useEffect(() => {
    saveState({ text, charName, userRole, card, sessionId, messages, textSummary, textLength, history, avatar });
  }, [text, charName, userRole, card, sessionId, messages, textSummary, textLength, history, avatar]);

  // 自动摘要
  React.useEffect(() => {
    const { summary } = summarizeMessages(messages);
    setSummaryText(summary);
  }, [messages]);

  const scrollToBottom = () => {
    if (messagesEndRef.current) messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
  };
  React.useEffect(scrollToBottom, [messages, sending]);

  // 文件读取（支持大文件，多格式）
  const readFile = (file) => {
    if (!file) return;
    const ext = file.name.split('.').pop().toLowerCase();
    if (!['txt', 'md', 'json', 'csv', 'log'].includes(ext)) {
      setError(`不支持 .${ext} 格式，请上传 .txt / .md / .json / .csv 文件`);
      return;
    }
    if (file.size > 100 * 1024 * 1024) {
      setError('文件超过 100MB 限制');
      return;
    }
    setError('');
    const reader = new FileReader();
    reader.onload = (ev) => setText(ev.target.result || '');
    reader.onerror = () => setError('文件读取失败');
    reader.readAsText(file, 'utf-8');
  };

  const onDragOver = (e) => { e.preventDefault(); setDragover(true); };
  const onDragLeave = () => setDragover(false);
  const onDrop = (e) => { e.preventDefault(); setDragover(false); readFile(e.dataTransfer?.files?.[0]); };
  const onFileSelect = (e) => readFile(e.target?.files?.[0]);

  // 头像上传
  const onAvatarSelect = (e) => {
    const file = e.target?.files?.[0];
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) { setError('头像图片不能超过 2MB'); return; }
    const reader = new FileReader();
    reader.onload = (ev) => setAvatar(ev.target.result);
    reader.readAsDataURL(file);
  };

  // 蒸馏
  const doDistill = async () => {
    const rawText = text.trim();
    if (!rawText) { setError('请先输入或上传文本'); return; }
    setLoading(true); setError(''); setCard(null);
    setMessages([]); setSessionId('');

    try {
      let targetName = charName.trim();

      if (!targetName) {
        setLoadingMsg('正在识别角色...');
        const idRes = await fetchT('/api/identify', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: rawText }),
        });
        if (!idRes.ok) { const e = await idRes.json().catch(() => ({})); throw new Error(e.detail || '角色识别失败'); }
        const idData = await idRes.json();
        if (!idData.characters || !idData.characters.length) throw new Error('未识别到角色，请手动输入角色名');
        const merged = mergeCharacters(idData.characters);
        targetName = merged[0].name;
      }

      setLoadingMsg('正在分析角色DNA...（大文本约需1-2分钟）');

      // 蒸馏请求，失败自动重试一次（LLM 偶尔格式错误）
      let cardData = null;
      for (let attempt = 0; attempt < 2; attempt++) {
        const distRes = await fetchT('/api/distill', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: rawText, character_name: targetName }),
        });
        if (distRes.ok) {
          cardData = await distRes.json();
          break;
        }
        const errBody = await distRes.json().catch(() => ({}));
        if (attempt === 0 && (errBody.detail || '').includes('格式不正确')) {
          setLoadingMsg('首次分析格式异常，自动重试...');
          continue;
        }
        throw new Error(errBody.detail || '蒸馏失败');
      }
      if (!cardData) throw new Error('蒸馏两次均失败，请尝试缩短文本或手动指定角色名');
      setCard(cardData);
      setSessionId(cardData.session_id);

      const summary = rawText.slice(0, 120).replace(/\n/g, ' ') + (rawText.length > 120 ? '...' : '');
      setTextSummary(summary);
      setTextLength(rawText.length);

      setHistory(prev => [{
        card: cardData, sessionId: cardData.session_id,
        textSummary: summary, textLength: rawText.length,
        avatar, createdAt: new Date().toLocaleString('zh-CN'),
      }, ...prev.filter(h => h.card?.name !== cardData.name).slice(0, 9)]);

      const initMsgs = [];
      if (cardData.first_message) initMsgs.push({ role: 'char', text: cardData.first_message });
      setMessages(initMsgs);
    } catch (e) {
      if (e.name === 'AbortError') setError('请求超时（超过3分钟），请缩短文本或检查网络');
      else if (e.message === 'Failed to fetch') setError('网络请求失败，后端可能超时，请查看终端日志');
      else setError(e.message || '操作失败');
    } finally { setLoading(false); setLoadingMsg(''); }
  };

  // 聊天
  const sendMessage = async () => {
    const msg = chatInput.trim();
    if (!msg || !sessionId || sending) return;
    setChatInput('');
    setMessages(prev => [...prev, { role: 'user', text: msg }]);
    setSending(true);
    try {
      const res = await fetchT('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, message: msg }),
      }, 60000);
      if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || '对话失败'); }
      const data = await res.json();
      setMessages(prev => [...prev, { role: 'char', text: data.reply }]);
    } catch (e) {
      const m = e.name === 'AbortError' ? '回复超时，请重试' : e.message;
      setMessages(prev => [...prev, { role: 'char', text: `[错误] ${m}` }]);
    } finally { setSending(false); }
  };

  const resetChat = async () => {
    if (!sessionId) return;
    try { await fetch('/api/reset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_id: sessionId }) }); } catch (e) {}
    setMessages(card?.first_message ? [{ role: 'char', text: card.first_message }] : []);
  };

  const resetAll = () => {
    setCard(null); setSessionId(''); setMessages([]);
    setText(''); setCharName(''); setUserRole(''); setError('');
    setTextSummary(''); setTextLength(0); setAvatar(null);
  };

  const loadFromHistory = (entry) => {
    setCard(entry.card); setSessionId(entry.sessionId);
    setTextSummary(entry.textSummary); setTextLength(entry.textLength);
    setAvatar(entry.avatar || null);
    setMessages(entry.card?.first_message ? [{ role: 'char', text: entry.card.first_message }] : []);
    setError('');
  };

  const handleChatKey = (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } };

  const avatarBg = card ? hashColor(card.name || '') : '#7EC8C8';
  const { summary: foldedSummary, recent: recentMsgs } = summarizeMessages(messages);

  // 渲染头像
  const renderAvatar = (size, cls) => {
    if (avatar) return <div className={cls} style={{ width: size, height: size }}><img src={avatar} /></div>;
    return <div className={cls} style={{ background: avatarBg, width: size, height: size }}>{(card?.name || '?')[0]}</div>;
  };

  return (
    <div className="app-shell">
      {/* ═══ 左栏 ═══ */}
      <div className="left-panel">
        <div>
          <div className="logo-title">📖 角色模拟器</div>
          <div className="logo-sub">上传文本，蒸馏角色，沉浸对话</div>
        </div>

        <div className="left-scroll">
          {/* 文本输入 */}
          <div>
            <textarea
              className={`text-area${dragover ? ' dragover' : ''}`}
              rows={10}
              placeholder="粘贴小说、聊天记录、人物描写...&#10;支持拖拽 .txt .md .json .csv 文件"
              value={text}
              onChange={(e) => setText(e.target.value)}
              onDragOver={onDragOver}
              onDragLeave={onDragLeave}
              onDrop={onDrop}
              disabled={loading}
            />
            <div className="drop-hint">
              <span onClick={() => fileRef.current?.click()} style={{ cursor: 'pointer', color: 'var(--accent)' }}>
                📁 点击上传文件
              </span>
              （支持 .txt .md .json .csv，最大 100MB）
              <input ref={fileRef} type="file" accept=".txt,.md,.json,.csv,.log" onChange={onFileSelect} style={{ display: 'none' }} />
            </div>
          </div>

          {/* 角色名 */}
          <div>
            <div className="input-label">目标角色名（留空自动识别，支持别名合并）</div>
            <input
              className="name-input"
              placeholder="如：汪东城、大东"
              value={charName}
              onChange={(e) => setCharName(e.target.value)}
              disabled={loading}
            />
          </div>

          {/* 用户身份 */}
          <div>
            <div className="input-label">你的身份设定（可选，角色会以此对待你）</div>
            <input
              className="user-role-input"
              placeholder="如：他的室友、一个陌生人、采访记者..."
              value={userRole}
              onChange={(e) => setUserRole(e.target.value)}
              disabled={loading}
            />
          </div>

          {/* 头像上传 */}
          <div className="avatar-upload">
            <div className="avatar-preview">
              {avatar ? <img src={avatar} /> : '👤'}
            </div>
            <button className="avatar-upload-btn" onClick={() => avatarRef.current?.click()}>
              上传角色头像（可选）
            </button>
            <input ref={avatarRef} type="file" accept="image/*" onChange={onAvatarSelect} style={{ display: 'none' }} />
            {avatar && <button className="avatar-upload-btn" onClick={() => setAvatar(null)}>清除</button>}
          </div>

          {/* 蒸馏按钮 */}
          <button className="btn-primary" onClick={doDistill} disabled={loading}>
            {loading ? <span><span className="spinner"></span>{loadingMsg}</span> : '🔍 开始蒸馏'}
          </button>

          {error && <div className="error-box">⚠️ {error}</div>}

          {/* 角色卡 */}
          {card && (
            <div className="char-card">
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                {renderAvatar(44, 'avatar-preview')}
                <div>
                  <div className="char-name">{card.name}</div>
                  <div className="char-identity">{card.identity}</div>
                </div>
              </div>

              {textLength > 0 && (
                <div className="context-source">
                  <div className="context-source-title">📄 来源（{textLength.toLocaleString()} 字）</div>
                  <div className="context-source-text">{textSummary}</div>
                </div>
              )}

              {card.personality_traits?.length > 0 && (
                <div className="card-section">
                  <div className="card-section-label">性格</div>
                  <div className="pill-list">
                    {card.personality_traits.map((t, i) => <span key={i} className="pill">{t}</span>)}
                  </div>
                </div>
              )}

              {card.speaking_style?.catchphrases?.length > 0 && (
                <div className="card-section">
                  <div className="card-section-label">口癖</div>
                  {card.speaking_style.catchphrases.map((c, i) => (
                    <div key={i} className="catchphrase">「{c}」</div>
                  ))}
                </div>
              )}

              {card.inner_tensions?.length > 0 && (
                <div className="card-section">
                  <div className="card-section-label">内在矛盾</div>
                  <div className="pill-list">
                    {card.inner_tensions.map((t, i) => <span key={i} className="pill-tension">{t}</span>)}
                  </div>
                </div>
              )}

              {card.relationships?.length > 0 && (
                <div className="card-section">
                  <div className="card-section-label">关系</div>
                  {card.relationships.map((r, i) => (
                    <div key={i} className="relation-item">
                      <span className="relation-target">{r.target}</span> — {r.relation} — {r.attitude}
                    </div>
                  ))}
                </div>
              )}

              <div style={{ marginTop: 14 }}>
                <button className="btn-link" onClick={resetAll}>换个角色 →</button>
              </div>
            </div>
          )}

          {/* 历史角色 */}
          {history.length > 0 && !card && (
            <div className="history-section">
              <div className="card-section-label">📚 历史角色</div>
              {history.map((entry, i) => (
                <div key={i} className="history-item" onClick={() => loadFromHistory(entry)}>
                  <div className="history-avatar" style={{ background: hashColor(entry.card?.name || '') }}>
                    {entry.avatar ? <img src={entry.avatar} /> : (entry.card?.name || '?')[0]}
                  </div>
                  <div className="history-info">
                    <div className="history-name">{entry.card?.name}</div>
                    <div className="history-meta">{entry.card?.identity} · {entry.createdAt}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* ═══ 右栏 ═══ */}
      <div className="right-panel">
        {!card ? (
          <div className="chat-empty">
            <div>
              <div className="chat-empty-icon">📖</div>
              <div className="chat-empty-title">在左侧上传文本并蒸馏角色</div>
              <div className="chat-empty-sub">即可在这里与角色沉浸式对话</div>
              {history.length > 0 && <div className="chat-empty-sub" style={{ marginTop: 8 }}>或点击左侧历史角色继续</div>}
            </div>
          </div>
        ) : (
          <>
            <div className="chat-topbar">
              <div className="chat-topbar-avatar" style={{ background: avatarBg }}>
                {avatar ? <img src={avatar} /> : (card.name || '?')[0]}
              </div>
              <div className="chat-topbar-name">{card.name}</div>
              <div className="chat-topbar-badge">{card.identity}</div>
              {userRole && <div className="chat-topbar-badge" style={{ background: 'rgba(200,160,50,0.08)', borderColor: 'rgba(200,160,50,0.2)', color: '#6B5A10' }}>你：{userRole}</div>}
              <div className="chat-topbar-actions">
                <button className="btn-ghost" onClick={resetChat}>🔄 重置</button>
                <button className="btn-ghost" onClick={resetAll}>🔀 换角色</button>
              </div>
            </div>

            {textLength > 0 && messages.length <= 1 && (
              <div className="context-banner">
                💡 角色基于 {textLength.toLocaleString()} 字文本生成{userRole ? `，你的身份：${userRole}` : ''}
              </div>
            )}

            <div className="chat-messages">
              {/* 折叠摘要 */}
              {foldedSummary && (
                <div className="summary-block">
                  <div className="summary-label">📋 历史摘要</div>
                  {foldedSummary}
                </div>
              )}

              {(foldedSummary ? recentMsgs : messages).map((m, i) => (
                <div key={i} className={`msg ${m.role === 'user' ? 'msg-user' : 'msg-char'}`}>
                  {m.role === 'char' && (
                    <div className="msg-avatar" style={{ background: avatarBg }}>
                      {avatar ? <img src={avatar} /> : (card.name || '?')[0]}
                    </div>
                  )}
                  <div className="msg-bubble">
                    {m.role === 'char' && <div className="msg-label">{card.name}</div>}
                    {m.text}
                  </div>
                </div>
              ))}
              {sending && (
                <div className="msg msg-char">
                  <div className="msg-avatar" style={{ background: avatarBg }}>
                    {avatar ? <img src={avatar} /> : (card.name || '?')[0]}
                  </div>
                  <div className="msg-bubble">
                    <div className="msg-label">{card.name}</div>
                    <div className="loading-dots"><span></span><span></span><span></span></div>
                  </div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>

            <div className="chat-input-bar">
              <div className="chat-input-wrap">
                <textarea
                  className="chat-input"
                  placeholder="输入消息…（Enter 发送，Shift+Enter 换行）"
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  onKeyDown={handleChatKey}
                  disabled={sending}
                  rows={1}
                />
                <button className="btn-send" onClick={sendMessage} disabled={!chatInput.trim() || sending}>
                  <svg viewBox="0 0 24 24"><path d="M12 4l0 16m0-16l-6 6m6-6l6 6"/></svg>
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

window.DesktopApp = DesktopApp;
ReactDOM.createRoot(document.getElementById('root')).render(<DesktopApp />);
