// desktop-app.jsx — 桌面全屏角色模拟器（稳定版）

const AVATAR_COLORS = ['#C9D8F5', '#E8D87A', '#7EC8C8', '#A8D878'];
function hashColor(name) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = ((h << 5) - h + name.charCodeAt(i)) | 0;
  return AVATAR_COLORS[Math.abs(h) % AVATAR_COLORS.length];
}

const LS_KEY = 'char_sim_s';
function loadLS() { try { return JSON.parse(localStorage.getItem(LS_KEY)) || {}; } catch(e) { return {}; } }
function saveLS(s) { try { localStorage.setItem(LS_KEY, JSON.stringify(s)); } catch(e) {} }

function fetchT(url, opts, ms) {
  ms = ms || 180000;
  var c = new AbortController();
  var t = setTimeout(function() { c.abort(); }, ms);
  return fetch(url, Object.assign({}, opts, { signal: c.signal })).finally(function() { clearTimeout(t); });
}

function DesktopApp() {
  var saved = React.useMemo(function() { return loadLS(); }, []);

  var _text = React.useState(saved.text || '');
  var text = _text[0], setText = _text[1];
  var _charName = React.useState(saved.charName || '');
  var charName = _charName[0], setCharName = _charName[1];
  var _userRole = React.useState(saved.userRole || '');
  var userRole = _userRole[0], setUserRole = _userRole[1];
  var _loading = React.useState(false);
  var loading = _loading[0], setLoading = _loading[1];
  var _loadingMsg = React.useState('');
  var loadingMsg = _loadingMsg[0], setLoadingMsg = _loadingMsg[1];
  var _error = React.useState('');
  var error = _error[0], setError = _error[1];
  var _card = React.useState(saved.card || null);
  var card = _card[0], setCard = _card[1];
  var _sessionId = React.useState(saved.sessionId || '');
  var sessionId = _sessionId[0], setSessionId = _sessionId[1];
  var _messages = React.useState(saved.messages || []);
  var messages = _messages[0], setMessages = _messages[1];
  var _chatInput = React.useState('');
  var chatInput = _chatInput[0], setChatInput = _chatInput[1];
  var _sending = React.useState(false);
  var sending = _sending[0], setSending = _sending[1];
  var _dragover = React.useState(false);
  var dragover = _dragover[0], setDragover = _dragover[1];
  var _avatar = React.useState(saved.avatar || null);
  var avatar = _avatar[0], setAvatar = _avatar[1];
  var _history = React.useState(saved.history || []);
  var history = _history[0], setHistory = _history[1];

  var messagesEndRef = React.useRef(null);
  var fileRef = React.useRef(null);
  var avatarRef = React.useRef(null);

  React.useEffect(function() {
    saveLS({ text: text, charName: charName, userRole: userRole, card: card, sessionId: sessionId, messages: messages, avatar: avatar, history: history });
  }, [text, charName, userRole, card, sessionId, messages, avatar, history]);

  React.useEffect(function() {
    if (messagesEndRef.current) messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
  }, [messages, sending]);

  function readFile(file) {
    if (!file) return;
    var ext = file.name.split('.').pop().toLowerCase();
    if (['txt','md','json','csv','log'].indexOf(ext) === -1) { setError('不支持该格式'); return; }
    if (file.size > 100*1024*1024) { setError('文件超过100MB'); return; }
    setError('');
    var reader = new FileReader();
    reader.onload = function(ev) { setText(ev.target.result || ''); };
    reader.onerror = function() { setError('文件读取失败'); };
    reader.readAsText(file, 'utf-8');
  }

  function onAvatarSelect(e) {
    var file = e.target && e.target.files && e.target.files[0];
    if (!file) return;
    if (file.size > 2*1024*1024) { setError('头像不能超过2MB'); return; }
    var reader = new FileReader();
    reader.onload = function(ev) { setAvatar(ev.target.result); };
    reader.readAsDataURL(file);
  }

  function doDistill() {
    var rawText = text.trim();
    if (!rawText) { setError('请先输入或上传文本'); return; }
    setLoading(true); setError(''); setCard(null); setMessages([]); setSessionId('');

    var targetName = charName.trim();
    var p = Promise.resolve(targetName);

    if (!targetName) {
      setLoadingMsg('正在识别角色...');
      p = fetchT('/api/identify', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: rawText })
      }).then(function(res) {
        if (!res.ok) return res.json().catch(function(){return {};}).then(function(e){throw new Error(e.detail||'识别失败');});
        return res.json();
      }).then(function(data) {
        if (!data.characters || !data.characters.length) throw new Error('未识别到角色，请手动输入角色名');
        return data.characters[0].name;
      });
    }

    p.then(function(name) {
      setLoadingMsg('正在分析角色DNA...');
      return fetchT('/api/distill', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: rawText, character_name: name })
      });
    }).then(function(res) {
      if (!res.ok) return res.json().catch(function(){return {};}).then(function(e){throw new Error(e.detail||'蒸馏失败');});
      return res.json();
    }).then(function(cardData) {
      setCard(cardData);
      setSessionId(cardData.session_id);
      if (cardData.first_message) setMessages([{ role: 'char', text: cardData.first_message }]);
      setHistory(function(prev) {
        var entry = { card: cardData, sessionId: cardData.session_id, avatar: avatar, createdAt: new Date().toLocaleString('zh-CN') };
        return [entry].concat(prev.filter(function(h){return h.card && h.card.name !== cardData.name;}).slice(0,9));
      });
    }).catch(function(e) {
      setError(e.message || '操作失败');
    }).finally(function() {
      setLoading(false); setLoadingMsg('');
    });
  }

  function sendMessage() {
    var msg = chatInput.trim();
    if (!msg || !sessionId || sending) return;
    setChatInput('');
    setMessages(function(prev) { return prev.concat([{ role: 'user', text: msg }]); });
    setSending(true);

    fetchT('/api/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, message: msg })
    }, 60000).then(function(res) {
      if (!res.ok) return res.json().catch(function(){return {};}).then(function(e){throw new Error(e.detail||'对话失败');});
      return res.json();
    }).then(function(data) {
      setMessages(function(prev) { return prev.concat([{ role: 'char', text: data.reply }]); });
    }).catch(function(e) {
      setMessages(function(prev) { return prev.concat([{ role: 'char', text: '[错误] ' + e.message }]); });
    }).finally(function() { setSending(false); });
  }

  function resetChat() {
    if (!sessionId) return;
    fetch('/api/reset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_id: sessionId }) }).catch(function(){});
    setMessages(card && card.first_message ? [{ role: 'char', text: card.first_message }] : []);
  }

  function resetAll() {
    setCard(null); setSessionId(''); setMessages([]);
    setText(''); setCharName(''); setUserRole(''); setError(''); setAvatar(null);
  }

  function loadFromHistory(entry) {
    setCard(entry.card); setSessionId(entry.sessionId);
    setAvatar(entry.avatar || null); setError('');
    setMessages(entry.card && entry.card.first_message ? [{ role: 'char', text: entry.card.first_message }] : []);
  }

  function handleChatKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  }

  var avatarBg = card ? hashColor(card.name || '') : '#7EC8C8';

  return (
    <div className="app-shell">
      <div className="left-panel">
        <div>
          <div className="logo-title">📖 角色模拟器</div>
          <div className="logo-sub">上传文本，蒸馏角色，沉浸对话</div>
        </div>
        <div className="left-scroll">
          <div>
            <textarea className={'text-area' + (dragover ? ' dragover' : '')} rows={10}
              placeholder="粘贴小说、聊天记录、人物描写..."
              value={text} onChange={function(e){setText(e.target.value);}}
              onDragOver={function(e){e.preventDefault();setDragover(true);}}
              onDragLeave={function(){setDragover(false);}}
              onDrop={function(e){e.preventDefault();setDragover(false);readFile(e.dataTransfer&&e.dataTransfer.files&&e.dataTransfer.files[0]);}}
              disabled={loading} />
            <div className="drop-hint">
              <span onClick={function(){fileRef.current&&fileRef.current.click();}} style={{cursor:'pointer',color:'var(--accent)'}}>📁 点击上传文件</span>
              （.txt .md .json .csv，最大100MB）
              <input ref={fileRef} type="file" accept=".txt,.md,.json,.csv,.log" onChange={function(e){readFile(e.target&&e.target.files&&e.target.files[0]);}} style={{display:'none'}} />
            </div>
          </div>
          <div>
            <div className="input-label">目标角色名（留空自动识别）</div>
            <input className="name-input" placeholder="如：汪东城" value={charName} onChange={function(e){setCharName(e.target.value);}} disabled={loading} />
          </div>
          <div>
            <div className="input-label">你的身份设定（可选）</div>
            <input className="user-role-input" placeholder="如：他的室友、采访记者..." value={userRole} onChange={function(e){setUserRole(e.target.value);}} disabled={loading} />
          </div>
          <div className="avatar-upload">
            <div className="avatar-preview">{avatar ? <img src={avatar} /> : '👤'}</div>
            <button className="avatar-upload-btn" onClick={function(){avatarRef.current&&avatarRef.current.click();}}>上传角色头像</button>
            <input ref={avatarRef} type="file" accept="image/*" onChange={onAvatarSelect} style={{display:'none'}} />
            {avatar && <button className="avatar-upload-btn" onClick={function(){setAvatar(null);}}>清除</button>}
          </div>
          <button className="btn-primary" onClick={doDistill} disabled={loading}>
            {loading ? <span><span className="spinner"></span>{loadingMsg}</span> : '🔍 开始蒸馏'}
          </button>
          {error && <div className="error-box">⚠️ {error}</div>}
          {card && (
            <div className="char-card">
              <div style={{display:'flex',alignItems:'center',gap:12}}>
                <div className="avatar-preview" style={{width:44,height:44,background:avatarBg}}>
                  {avatar ? <img src={avatar} /> : <span style={{width:'100%',height:'100%',display:'flex',alignItems:'center',justifyContent:'center',borderRadius:'50%',color:'#fff',fontWeight:700,fontSize:18}}>{(card.name||'?')[0]}</span>}
                </div>
                <div>
                  <div className="char-name">{card.name}</div>
                  <div className="char-identity">{card.identity}</div>
                </div>
              </div>
              {card.personality_traits && card.personality_traits.length > 0 && (
                <div className="card-section"><div className="card-section-label">性格</div><div className="pill-list">{card.personality_traits.map(function(t,i){return <span key={i} className="pill">{t}</span>;})}</div></div>
              )}
              {card.speaking_style && card.speaking_style.catchphrases && card.speaking_style.catchphrases.length > 0 && (
                <div className="card-section"><div className="card-section-label">口癖</div>{card.speaking_style.catchphrases.map(function(c,i){return <div key={i} className="catchphrase">「{c}」</div>;})}</div>
              )}
              {card.inner_tensions && card.inner_tensions.length > 0 && (
                <div className="card-section"><div className="card-section-label">内在矛盾</div><div className="pill-list">{card.inner_tensions.map(function(t,i){return <span key={i} className="pill-tension">{t}</span>;})}</div></div>
              )}
              {card.relationships && card.relationships.length > 0 && (
                <div className="card-section"><div className="card-section-label">关系</div>{card.relationships.map(function(r,i){return <div key={i} className="relation-item"><span className="relation-target">{r.target}</span> — {r.relation} — {r.attitude}</div>;})}</div>
              )}
              <div style={{marginTop:14}}><button className="btn-link" onClick={resetAll}>换个角色 →</button></div>
            </div>
          )}
          {history.length > 0 && !card && (
            <div className="history-section">
              <div className="card-section-label">📚 历史角色</div>
              {history.map(function(entry,i){
                return (
                  <div key={i} className="history-item" onClick={function(){loadFromHistory(entry);}}>
                    <div className="history-avatar" style={{background:hashColor(entry.card&&entry.card.name||'')}}>{entry.avatar ? <img src={entry.avatar}/> : (entry.card&&entry.card.name||'?')[0]}</div>
                    <div className="history-info">
                      <div className="history-name">{entry.card&&entry.card.name}</div>
                      <div className="history-meta">{entry.card&&entry.card.identity} · {entry.createdAt}</div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      <div className="right-panel">
        {!card ? (
          <div className="chat-empty">
            <div>
              <div className="chat-empty-icon">📖</div>
              <div className="chat-empty-title">在左侧上传文本并蒸馏角色</div>
              <div className="chat-empty-sub">即可在这里与角色沉浸式对话</div>
            </div>
          </div>
        ) : (
          <React.Fragment>
            <div className="chat-topbar">
              <div className="chat-topbar-avatar" style={{background:avatarBg}}>{avatar ? <img src={avatar}/> : (card.name||'?')[0]}</div>
              <div className="chat-topbar-name">{card.name}</div>
              <div className="chat-topbar-badge">{card.identity}</div>
              {userRole && <div className="chat-topbar-badge" style={{background:'rgba(200,160,50,0.08)',borderColor:'rgba(200,160,50,0.2)',color:'#6B5A10'}}>你：{userRole}</div>}
              <div className="chat-topbar-actions">
                <button className="btn-ghost" onClick={resetChat}>🔄 重置</button>
                <button className="btn-ghost" onClick={resetAll}>🔀 换角色</button>
              </div>
            </div>
            <div className="chat-messages">
              {messages.map(function(m,i){
                return (
                  <div key={i} className={'msg ' + (m.role==='user'?'msg-user':'msg-char')}>
                    {m.role==='char' && <div className="msg-avatar" style={{background:avatarBg}}>{avatar ? <img src={avatar}/> : (card.name||'?')[0]}</div>}
                    <div className="msg-bubble">
                      {m.role==='char' && <div className="msg-label">{card.name}</div>}
                      {m.text}
                    </div>
                  </div>
                );
              })}
              {sending && (
                <div className="msg msg-char">
                  <div className="msg-avatar" style={{background:avatarBg}}>{(card.name||'?')[0]}</div>
                  <div className="msg-bubble"><div className="msg-label">{card.name}</div><div className="loading-dots"><span></span><span></span><span></span></div></div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>
            <div className="chat-input-bar">
              <div className="chat-input-wrap">
                <textarea className="chat-input" placeholder="输入消息…（Enter 发送，Shift+Enter 换行）" value={chatInput} onChange={function(e){setChatInput(e.target.value);}} onKeyDown={handleChatKey} disabled={sending} rows={1} />
                <button className="btn-send" onClick={sendMessage} disabled={!chatInput.trim()||sending}>
                  <svg viewBox="0 0 24 24"><path d="M12 4l0 16m0-16l-6 6m6-6l6 6"/></svg>
                </button>
              </div>
            </div>
          </React.Fragment>
        )}
      </div>
    </div>
  );
}

window.DesktopApp = DesktopApp;
ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(DesktopApp));
