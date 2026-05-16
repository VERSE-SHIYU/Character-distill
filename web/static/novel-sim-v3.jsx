
// novel-sim-v3.jsx — 接入后端 API 的完整版本

// ─── 默认预设角色（未蒸馏时展示） ─────────────────────────────
const CHARS_V3 = [];

// ─── Theme ──────────────────────────────────────────────────
const sage = {
  phoneBg: 'linear-gradient(180deg, rgba(200,225,210,0.25) 0%, #F6F9F4 30%, #F4F7F2 100%)',
  phoneBgFallback: '#F4F7F2',
  card: 'rgba(255,255,255,0.80)',
  cardBorder: 'rgba(255,255,255,0.65)',
  searchBg: 'rgba(255,255,255,0.55)',
  searchBorder: 'rgba(0,0,0,0.04)',
  tabBg: 'rgba(255,255,255,0.72)',

  text: '#1C2B1F',
  textSec: '#6B7D6F',
  textTer: '#9DAA9F',

  tagBg: 'rgba(107,191,138,0.12)',
  tagText: '#4A8A5E',
  tagBorder: 'rgba(107,191,138,0.22)',

  accent: '#5BA87A',
  accentBlue: '#5AAED6',

  bubbleUser: '#5BA87A',
  bubbleUserText: '#fff',
  bubbleChar: 'rgba(255,255,255,0.85)',
  bubbleCharText: '#1C2B1F',

  divider: 'rgba(0,0,0,0.05)',
};

// ─── 颜色池 ──────────────────────────────────────────────────
const COLOR_POOL = ['#9E96CC', '#C4AA3C', '#5CB8E8', '#5EAF6E', '#D4836B', '#6BA3D6', '#A8A0D0'];

// ─── SVG Icons ──────────────────────────────────────────────
const SvgSearch = () => (
  <svg width="15" height="15" viewBox="0 0 16 16" fill="none">
    <circle cx="7" cy="7" r="5.5" stroke={sage.textTer} strokeWidth="1.3"/>
    <path d="M11 11L14 14" stroke={sage.textTer} strokeWidth="1.3" strokeLinecap="round"/>
  </svg>
);

const SvgBack = () => (
  <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
    <path d="M12.5 15L7.5 10L12.5 5" stroke={sage.textSec} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
  </svg>
);

const SvgSend = ({ active }) => (
  <svg width="18" height="18" viewBox="0 0 20 20" fill="none">
    <path d="M3 10L17 3L14 10L17 17L3 10Z" fill={active ? '#fff' : sage.textTer} />
  </svg>
);

const SvgBookmark = () => (
  <svg width="18" height="18" viewBox="0 0 20 20" fill="none" style={{ opacity: 0.18 }}>
    <path d="M5 3h10a1 1 0 011 1v13l-6-3.5L4 17V4a1 1 0 011-1z" stroke={sage.text} strokeWidth="1.3" strokeLinejoin="round"/>
  </svg>
);

// Tab icons
const SvgTabChat = ({ active }) => (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
    <path d="M12 3C7.03 3 3 6.58 3 11c0 2.06.87 3.94 2.3 5.37L4 21l4.63-1.3C9.7 19.9 10.83 20 12 20c4.97 0 9-3.13 9-7s-4.03-8-9-8z"
      stroke={active ? sage.accentBlue : sage.textTer} strokeWidth="1.5"
      fill={active ? 'rgba(90,174,214,0.1)' : 'none'}/>
  </svg>
);
const SvgTabBook = ({ active }) => (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
    <path d="M4 4.5A2.5 2.5 0 016.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15z"
      stroke={active ? sage.accentBlue : sage.textTer} strokeWidth="1.5"
      fill={active ? 'rgba(90,174,214,0.1)' : 'none'}/>
    <path d="M8 7h8M8 11h5" stroke={active ? sage.accentBlue : sage.textTer} strokeWidth="1.3" strokeLinecap="round"/>
  </svg>
);
const SvgTabUser = ({ active }) => (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
    <circle cx="12" cy="8" r="4" stroke={active ? sage.accentBlue : sage.textTer} strokeWidth="1.5"
      fill={active ? 'rgba(90,174,214,0.1)' : 'none'}/>
    <path d="M5 20c0-3.31 3.13-6 7-6s7 2.69 7 6" stroke={active ? sage.accentBlue : sage.textTer}
      strokeWidth="1.5" strokeLinecap="round"/>
  </svg>
);

// ─── Tab Bar ────────────────────────────────────────────────
function TabBar({ active, onChange }) {
  const tabs = [
    { id: 'chars', label: '角色', Icon: SvgTabChat },
    { id: 'distill', label: '蒸馏', Icon: SvgTabBook },
    { id: 'me', label: '我的', Icon: SvgTabUser },
  ];
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-around',
      padding: '10px 0 4px',
      background: sage.tabBg,
      backdropFilter: 'blur(20px) saturate(160%)',
      WebkitBackdropFilter: 'blur(20px) saturate(160%)',
      borderTop: `0.5px solid rgba(0,0,0,0.05)`,
    }}>
      {tabs.map(({ id, label, Icon }) => {
        const on = active === id;
        return (
          <button key={id} onClick={() => onChange(id)} style={{
            appearance: 'none', border: 'none', background: 'none',
            display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3,
            padding: '2px 24px', cursor: 'pointer',
          }}>
            <Icon active={on} />
            <span style={{
              fontSize: 10, fontWeight: 500, letterSpacing: -0.1,
              color: on ? sage.accentBlue : sage.textTer,
            }}>{label}</span>
          </button>
        );
      })}
    </div>
  );
}

// ─── Character Row ──────────────────────────────────────────
function CharRowV3({ char, onClick }) {
  const [hover, setHover] = React.useState(false);
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        appearance: 'none', border: 'none', textAlign: 'left', width: '100%',
        display: 'flex', alignItems: 'center', gap: 14,
        padding: '16px 16px',
        background: hover ? 'rgba(0,0,0,0.02)' : 'transparent',
        cursor: 'pointer', transition: 'background 0.15s',
      }}
    >
      <div style={{
        width: 50, height: 50, borderRadius: 25,
        background: char.color,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 22, fontWeight: 600, color: '#fff',
        flexShrink: 0,
        boxShadow: `0 3px 10px ${char.color}35`,
      }}>{char.avatar}</div>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{
            fontSize: 16.5, fontWeight: 700, color: sage.text,
            letterSpacing: -0.3,
          }}>{char.name}</span>
          <span style={{
            fontSize: 11, fontWeight: 500,
            color: sage.tagText, background: sage.tagBg,
            border: `0.5px solid ${sage.tagBorder}`,
            padding: '2.5px 10px', borderRadius: 10,
            letterSpacing: 0.3, lineHeight: 1,
            whiteSpace: 'nowrap', flexShrink: 0,
          }}>{char.tag}</span>
        </div>
        <div style={{
          fontSize: 13.5, color: sage.textSec, marginTop: 6,
          lineHeight: 1.4, letterSpacing: -0.1,
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        }}>{char.greeting}</div>
      </div>

      <SvgBookmark />
    </button>
  );
}

// ─── List Screen ────────────────────────────────────────────
function ListScreenV3({ chars, onSelect, tab, setTab }) {
  const [search, setSearch] = React.useState('');
  const filtered = chars.filter(c =>
    !search || c.name.includes(search) || c.tag.includes(search)
  );

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', height: '100%',
      background: sage.phoneBg, backgroundColor: sage.phoneBgFallback,
    }}>
      <div style={{ flex: 1, overflow: 'auto' }}>
        <div style={{
          padding: '20px 22px 18px',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <h1 style={{
            fontSize: 36, fontWeight: 760, color: sage.text,
            letterSpacing: -1, lineHeight: 1.1, margin: 0,
          }}>角色</h1>
          <div style={{
            width: 42, height: 42, borderRadius: 21,
            background: 'linear-gradient(135deg, #6BC0E8 0%, #5AAED6 100%)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            boxShadow: '0 3px 12px rgba(90,174,214,0.35)',
          }}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
              <circle cx="12" cy="8" r="4" stroke="#fff" strokeWidth="1.8"/>
              <path d="M5 20c0-3.31 3.13-6 7-6s7 2.69 7 6" stroke="#fff" strokeWidth="1.8" strokeLinecap="round"/>
            </svg>
          </div>
        </div>

        <div style={{ padding: '0 16px 18px' }}>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 10,
            background: sage.searchBg,
            backdropFilter: 'blur(12px)', WebkitBackdropFilter: 'blur(12px)',
            border: `0.5px solid ${sage.searchBorder}`,
            borderRadius: 16, padding: '0 16px', height: 46,
          }}>
            <SvgSearch />
            <input value={search} onChange={e => setSearch(e.target.value)}
              placeholder="搜索角色…"
              style={{
                flex: 1, border: 'none', outline: 'none', background: 'none',
                fontSize: 15, color: sage.text, letterSpacing: -0.15,
              }} />
          </div>
        </div>

        {chars.length === 0 ? (
          <div style={{ padding: '60px 30px', textAlign: 'center' }}>
            <div style={{ fontSize: 40, marginBottom: 12 }}>📖</div>
            <div style={{ fontSize: 15, color: sage.textSec, lineHeight: 1.6 }}>
              还没有角色<br/>去「蒸馏」页面上传文本<br/>自动提取角色吧
            </div>
          </div>
        ) : (
          <>
            <div style={{
              padding: '2px 22px 10px',
              fontSize: 13.5, fontWeight: 500, color: sage.textSec,
              letterSpacing: -0.1,
            }}>已蒸馏角色</div>

            <div style={{
              margin: '0 14px',
              background: sage.card,
              backdropFilter: 'blur(16px)', WebkitBackdropFilter: 'blur(16px)',
              borderRadius: 22,
              border: `0.5px solid ${sage.cardBorder}`,
              boxShadow: '0 1px 8px rgba(0,0,0,0.03)',
              overflow: 'hidden',
            }}>
              {filtered.map((c, i) => (
                <React.Fragment key={c.id}>
                  <CharRowV3 char={c} onClick={() => onSelect(c)} />
                  {i < filtered.length - 1 && (
                    <div style={{ height: 0.5, background: sage.divider, marginLeft: 80 }} />
                  )}
                </React.Fragment>
              ))}
              {filtered.length === 0 && (
                <div style={{ padding: 40, textAlign: 'center', fontSize: 14, color: sage.textTer }}>
                  未找到匹配角色
                </div>
              )}
            </div>
          </>
        )}
        <div style={{ height: 24 }} />
      </div>

      <TabBar active={tab} onChange={setTab} />
    </div>
  );
}

// ─── Distill Screen (蒸馏页) ────────────────────────────────
function DistillScreenV3({ tab, setTab, onDistilled }) {
  const [text, setText] = React.useState('');
  const [charName, setCharName] = React.useState('');
  const [loading, setLoading] = React.useState(false);
  const [result, setResult] = React.useState(null);
  const [error, setError] = React.useState('');

  const doDistill = async () => {
    if (!text.trim()) { setError('请输入文本'); return; }
    setLoading(true); setError(''); setResult(null);
    try {
      const res = await fetch('/api/distill', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: text.trim(), character_name: charName.trim() }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `请求失败 ${res.status}`);
      }
      const card = await res.json();
      setResult(card);
      if (onDistilled) onDistilled(card);
    } catch (e) {
      setError(e.message || '蒸馏失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', height: '100%',
      background: sage.phoneBg, backgroundColor: sage.phoneBgFallback,
    }}>
      <div style={{ flex: 1, overflow: 'auto' }}>
        <div style={{ padding: '20px 22px 12px' }}>
          <h1 style={{
            fontSize: 36, fontWeight: 760, color: sage.text,
            letterSpacing: -1, lineHeight: 1.1, margin: 0,
          }}>蒸馏</h1>
          <div style={{
            fontSize: 13.5, color: sage.textSec, marginTop: 8, lineHeight: 1.5,
          }}>粘贴小说/聊天记录，自动提取角色</div>
        </div>

        <div style={{ padding: '0 16px', display: 'flex', flexDirection: 'column', gap: 12 }}>
          <textarea
            value={text} onChange={e => setText(e.target.value)}
            placeholder="把文本粘贴到这里…"
            rows={6}
            style={{
              width: '100%', boxSizing: 'border-box', resize: 'vertical',
              background: sage.searchBg, border: `0.5px solid ${sage.searchBorder}`,
              borderRadius: 16, padding: '14px 16px',
              fontSize: 14, color: sage.text, lineHeight: 1.6,
              outline: 'none', fontFamily: 'inherit',
            }}
          />
          <input
            value={charName} onChange={e => setCharName(e.target.value)}
            placeholder="角色名（留空自动识别主角）"
            style={{
              width: '100%', boxSizing: 'border-box', height: 44,
              background: sage.searchBg, border: `0.5px solid ${sage.searchBorder}`,
              borderRadius: 16, padding: '0 16px',
              fontSize: 14, color: sage.text, outline: 'none', fontFamily: 'inherit',
            }}
          />
          <button onClick={doDistill} disabled={loading}
            style={{
              width: '100%', height: 48, borderRadius: 16,
              background: loading ? sage.textTer : sage.accent,
              color: '#fff', border: 'none', fontSize: 16, fontWeight: 600,
              cursor: loading ? 'wait' : 'pointer',
              letterSpacing: 0.5,
              boxShadow: loading ? 'none' : `0 4px 14px ${sage.accent}40`,
              transition: 'all 0.2s',
            }}>
            {loading ? '蒸馏中…请等待' : '🧬 开始蒸馏'}
          </button>
        </div>

        {error && (
          <div style={{
            margin: '12px 16px', padding: '12px 16px',
            background: 'rgba(220,60,60,0.08)', borderRadius: 14,
            fontSize: 13.5, color: '#B33', lineHeight: 1.5,
          }}>{error}</div>
        )}

        {result && (
          <div style={{
            margin: '16px 14px',
            background: sage.card, borderRadius: 22,
            border: `0.5px solid ${sage.cardBorder}`,
            boxShadow: '0 1px 8px rgba(0,0,0,0.03)',
            padding: '18px 18px 14px', overflow: 'hidden',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 }}>
              <div style={{
                width: 44, height: 44, borderRadius: 22,
                background: sage.accent,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 20, fontWeight: 600, color: '#fff',
              }}>{(result.name || '?')[0]}</div>
              <div>
                <div style={{ fontSize: 17, fontWeight: 700, color: sage.text }}>{result.name}</div>
                <div style={{ fontSize: 13, color: sage.textSec, marginTop: 2 }}>{result.identity}</div>
              </div>
            </div>
            {[
              ['性格', (result.personality_traits || []).join('；')],
              ['口癖', ((result.speaking_style || {}).catchphrases || []).join('；')],
              ['内在矛盾', (result.inner_tensions || []).join('；')],
            ].map(([label, val]) => val && (
              <div key={label} style={{ marginBottom: 8 }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: sage.tagText }}>{label}</span>
                <div style={{ fontSize: 13, color: sage.textSec, lineHeight: 1.5, marginTop: 2 }}>{val}</div>
              </div>
            ))}
            <div style={{
              marginTop: 10, padding: '10px 14px', borderRadius: 14,
              background: 'rgba(107,191,138,0.08)',
              fontSize: 13, color: sage.accent, fontWeight: 500, textAlign: 'center',
            }}>✅ 切换到「角色」页开始对话</div>
          </div>
        )}

        <div style={{ height: 24 }} />
      </div>

      <TabBar active={tab} onChange={setTab} />
    </div>
  );
}

// ─── Chat Bubbles ───────────────────────────────────────────
function BubbleV3({ msg, char, tweaks }) {
  const isUser = msg.role === 'user';
  return (
    <div style={{
      display: 'flex', gap: 10,
      justifyContent: isUser ? 'flex-end' : 'flex-start',
      padding: '0 16px', alignItems: 'flex-end',
    }}>
      {!isUser && (
        <div style={{
          width: 32, height: 32, borderRadius: 16,
          background: char.color,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 14, fontWeight: 600, color: '#fff', flexShrink: 0,
          boxShadow: `0 2px 6px ${char.color}30`,
        }}>{char.avatar}</div>
      )}
      <div style={{
        maxWidth: '72%',
        background: isUser ? tweaks.bubbleColor : sage.bubbleChar,
        color: isUser ? sage.bubbleUserText : sage.bubbleCharText,
        borderRadius: isUser ? '20px 20px 4px 20px' : '20px 20px 20px 4px',
        padding: '11px 15px',
        fontSize: 15, lineHeight: 1.6, letterSpacing: -0.15,
        boxShadow: isUser ? `0 2px 8px ${tweaks.bubbleColor}25` : '0 1px 4px rgba(0,0,0,0.03)',
      }}>{msg.text}</div>
    </div>
  );
}

function DotsV3() {
  return (
    <div style={{ display: 'flex', gap: 4, padding: '6px 4px', alignItems: 'center' }}>
      {[0, 1, 2].map(i => (
        <div key={i} style={{
          width: 6, height: 6, borderRadius: '50%',
          background: sage.textTer,
          animation: `ns-typing 1s ease-in-out ${i * 0.13}s infinite`,
        }} />
      ))}
    </div>
  );
}

// ─── Chat Screen ────────────────────────────────────────────
function ChatScreenV3({ char, onBack, tweaks }) {
  const [msgs, setMsgs] = React.useState(
    char.greeting ? [{ role: 'char', text: char.greeting }] : []
  );
  const [typing, setTyping] = React.useState(false);
  const [val, setVal] = React.useState('');
  const scrollRef = React.useRef(null);

  React.useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [msgs, typing]);

  const send = async () => {
    if (!val.trim() || typing) return;
    const text = val.trim();
    setVal('');
    setMsgs(p => [...p, { role: 'user', text }]);
    setTyping(true);
    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: char.sessionId, message: text }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || '对话失败');
      }
      const data = await res.json();
      setMsgs(p => [...p, { role: 'char', text: data.reply }]);
    } catch (e) {
      setMsgs(p => [...p, { role: 'char', text: `[错误] ${e.message}` }]);
    } finally {
      setTyping(false);
    }
  };

  const active = val.trim() && !typing;

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', height: '100%',
      background: sage.phoneBg, backgroundColor: sage.phoneBgFallback,
    }}>
      {/* Nav */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '14px 14px 14px 8px',
        borderBottom: `0.5px solid ${sage.divider}`,
        background: 'rgba(244,247,242,0.9)',
        backdropFilter: 'blur(16px)', WebkitBackdropFilter: 'blur(16px)',
        position: 'sticky', top: 0, zIndex: 10,
      }}>
        <button onClick={onBack} style={{
          appearance: 'none', border: 'none', background: 'none',
          padding: 6, cursor: 'pointer', display: 'flex', borderRadius: 8,
        }}><SvgBack /></button>
        <div style={{
          width: 36, height: 36, borderRadius: 18,
          background: char.color,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 16, fontWeight: 600, color: '#fff', flexShrink: 0,
          boxShadow: `0 2px 8px ${char.color}30`,
        }}>{char.avatar}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 16, fontWeight: 640, color: sage.text, letterSpacing: -0.2 }}>
            {char.name}
          </div>
          <div style={{ fontSize: 12, color: sage.textTer, marginTop: 1, letterSpacing: -0.1 }}>
            {char.tag}
          </div>
        </div>
        <span style={{
          fontSize: 11, fontWeight: 500, color: sage.tagText,
          background: sage.tagBg, border: `0.5px solid ${sage.tagBorder}`,
          padding: '3px 10px', borderRadius: 10, lineHeight: 1, whiteSpace: 'nowrap',
        }}>在线</span>
      </div>

      {/* Messages */}
      <div ref={scrollRef} style={{
        flex: 1, overflow: 'auto', padding: '16px 0',
        display: 'flex', flexDirection: 'column', gap: 12,
      }}>
        {char.traits && char.traits.length > 0 && (
          <div style={{
            textAlign: 'center', padding: '4px 40px 12px',
            fontSize: 12, color: sage.textTer, lineHeight: 1.5,
          }}>
            {char.traits.join(' · ')}
          </div>
        )}
        {msgs.map((m, i) => (
          <BubbleV3 key={i} msg={m} char={char} tweaks={tweaks} />
        ))}
        {typing && (
          <div style={{ display: 'flex', gap: 10, padding: '0 16px', alignItems: 'flex-end' }}>
            <div style={{
              width: 32, height: 32, borderRadius: 16,
              background: char.color,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 14, fontWeight: 600, color: '#fff', flexShrink: 0,
            }}>{char.avatar}</div>
            <div style={{
              background: sage.bubbleChar, borderRadius: '20px 20px 20px 4px',
              padding: '11px 15px',
            }}><DotsV3 /></div>
          </div>
        )}
      </div>

      {/* Input */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '10px 14px 10px',
        borderTop: `0.5px solid ${sage.divider}`,
        background: 'rgba(244,247,242,0.9)',
        backdropFilter: 'blur(16px)', WebkitBackdropFilter: 'blur(16px)',
      }}>
        <div style={{
          flex: 1, display: 'flex', alignItems: 'center',
          background: sage.searchBg,
          border: `0.5px solid ${sage.searchBorder}`,
          borderRadius: 24, padding: '0 5px 0 16px', minHeight: 44,
          backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)',
        }}>
          <input
            value={val} onChange={e => setVal(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && send()}
            placeholder="输入消息…" disabled={typing}
            style={{
              flex: 1, border: 'none', outline: 'none', background: 'none',
              fontSize: 15, color: sage.text, padding: '10px 0', letterSpacing: -0.15,
            }}
          />
          <button onClick={send} disabled={!active}
            style={{
              width: 34, height: 34, borderRadius: 17,
              background: active ? tweaks.bubbleColor : 'rgba(0,0,0,0.04)',
              border: 'none', cursor: active ? 'pointer' : 'default',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              transition: 'all 0.15s', flexShrink: 0,
              boxShadow: active ? `0 2px 8px ${tweaks.bubbleColor}30` : 'none',
            }}
          ><SvgSend active={active} /></button>
        </div>
      </div>
    </div>
  );
}

// ─── Me Screen (占位) ────────────────────────────────────────
function MeScreenV3({ tab, setTab }) {
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', height: '100%',
      background: sage.phoneBg, backgroundColor: sage.phoneBgFallback,
    }}>
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div style={{ textAlign: 'center', color: sage.textTer, fontSize: 14, lineHeight: 1.8 }}>
          <div style={{ fontSize: 40, marginBottom: 8 }}>👤</div>
          我的<br/>更多功能即将上线
        </div>
      </div>
      <TabBar active={tab} onChange={setTab} />
    </div>
  );
}

// ─── App Shell ──────────────────────────────────────────────
function NovelAppV3({ tweaks }) {
  const [view, setView] = React.useState('list');
  const [char, setChar] = React.useState(null);
  const [chars, setChars] = React.useState([]);
  const [anim, setAnim] = React.useState(false);
  const [tab, setTab] = React.useState('chars');

  const handleDistilled = (card) => {
    const newChar = {
      id: card.session_id,
      sessionId: card.session_id,
      name: card.name,
      tag: card.identity ? card.identity.slice(0, 4) : '',
      avatar: (card.name || '?')[0],
      color: COLOR_POOL[chars.length % COLOR_POOL.length],
      greeting: card.first_message || `你好，我是${card.name}。`,
      traits: (card.personality_traits || []).slice(0, 3).map(t => t.slice(0, 6)),
    };
    setChars(prev => [newChar, ...prev]);
    setTab('chars');
  };

  const pick = (c) => {
    setChar(c);
    requestAnimationFrame(() => {
      setAnim(true);
      setTimeout(() => setView('chat'), 280);
    });
  };
  const back = () => {
    setAnim(false);
    setTimeout(() => { setView('list'); setChar(null); }, 280);
  };

  const currentScreen = () => {
    if (tab === 'distill') return <DistillScreenV3 tab={tab} setTab={setTab} onDistilled={handleDistilled} />;
    if (tab === 'me') return <MeScreenV3 tab={tab} setTab={setTab} />;
    return <ListScreenV3 chars={chars} onSelect={pick} tab={tab} setTab={setTab} />;
  };

  return (
    <div style={{ width: '100%', height: '100%', position: 'relative', overflow: 'hidden' }}>
      <div style={{
        position: 'absolute', inset: 0,
        transform: anim ? 'translateX(-25%)' : 'translateX(0)',
        opacity: anim ? 0 : 1,
        transition: 'transform 0.28s cubic-bezier(0.32,0.72,0,1), opacity 0.22s ease',
        pointerEvents: anim ? 'none' : 'auto',
      }}>
        {currentScreen()}
      </div>
      <div style={{
        position: 'absolute', inset: 0,
        transform: anim ? 'translateX(0)' : 'translateX(100%)',
        transition: 'transform 0.28s cubic-bezier(0.32,0.72,0,1)',
        pointerEvents: anim ? 'auto' : 'none',
      }}>
        {char && <ChatScreenV3 char={char} onBack={back} tweaks={tweaks} />}
      </div>
    </div>
  );
}

Object.assign(window, { NovelAppV3, CHARS_V3 });
