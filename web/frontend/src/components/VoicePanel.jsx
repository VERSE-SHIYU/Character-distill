import { useEffect, useState } from 'react'
import useAppStore from '../store/useAppStore'

export default function VoicePanel() {
  const voiceStatus = useAppStore((s) => s.voiceStatus)
  const voiceEnabled = useAppStore((s) => s.voiceEnabled)
  const setVoiceEnabled = useAppStore((s) => s.setVoiceEnabled)
  const voiceList = useAppStore((s) => s.voiceList)
  const loadVoices = useAppStore((s) => s.loadVoices)

  const [selectedVoice, setSelectedVoice] = useState(() =>
    localStorage.getItem('tts_voice') || 'xiaoxiao'
  )

  useEffect(() => { loadVoices() }, [loadVoices])

  const handleVoiceChange = (voice) => {
    setSelectedVoice(voice)
    localStorage.setItem('tts_voice', voice)
  }

  return (
    <div className="voice-panel panel">
      <header className="panel-header">
        <h1 className="panel-title">语音设置</h1>
        <p className="panel-desc">TTS 音色与语音服务状态</p>
      </header>

      <div className="voice-section">
        <label className="voice-toggle-row">
          <span className="voice-toggle-label">启用语音</span>
          <input
            type="checkbox"
            checked={voiceEnabled}
            onChange={(e) => setVoiceEnabled(e.target.checked)}
          />
        </label>

        {voiceEnabled && (
          <div className="voice-options">
            <h3 className="voice-options-title">音色选择</h3>
            <label className="voice-option">
              <input
                type="radio"
                name="voice"
                value="xiaoxiao"
                checked={selectedVoice === 'xiaoxiao'}
                onChange={() => handleVoiceChange('xiaoxiao')}
              />
              晓晓（女，活泼）
            </label>
            <label className="voice-option">
              <input
                type="radio"
                name="voice"
                value="yunxi"
                checked={selectedVoice === 'yunxi'}
                onChange={() => handleVoiceChange('yunxi')}
              />
              云希（男，青年）
            </label>
            <label className="voice-option">
              <input
                type="radio"
                name="voice"
                value="xiaoyi"
                checked={selectedVoice === 'xiaoyi'}
                onChange={() => handleVoiceChange('xiaoyi')}
              />
              晓伊（女，温柔）
            </label>
            <label className="voice-option">
              <input
                type="radio"
                name="voice"
                value="yunyang"
                checked={selectedVoice === 'yunyang'}
                onChange={() => handleVoiceChange('yunyang')}
              />
              云扬（男，新闻播报）
            </label>

            {voiceList.length > 0 && (
              <>
                <h3 className="voice-options-title">自定义音色</h3>
                {voiceList.map((v) => (
                  <label key={v.voice_id} className="voice-option">
                    <input
                      type="radio"
                      name="voice"
                      value={v.voice_id}
                      checked={selectedVoice === v.voice_id}
                      onChange={() => handleVoiceChange(v.voice_id)}
                    />
                    {v.name}
                  </label>
                ))}
              </>
            )}
          </div>
        )}

        <div className="voice-status">
          <p>
            <span className="voice-status-dot">{voiceStatus.gptsovits ? '\u{1F7E2}' : '\u{1F534}'}</span>
            GPT-SoVITS: {voiceStatus.gptsovits ? '已连接' : '未连接'}
          </p>
          <p>
            <span className="voice-status-dot">{voiceStatus.funasr ? '\u{1F7E2}' : '\u{1F534}'}</span>
            FunASR: {voiceStatus.funasr ? '已连接' : '未连接'}
          </p>
        </div>
      </div>
    </div>
  )
}
