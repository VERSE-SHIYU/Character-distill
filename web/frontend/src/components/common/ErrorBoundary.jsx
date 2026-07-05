import { Component } from 'react'

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null, stack: '' }
  }

  static getDerivedStateFromError(error) {
    return { error, stack: error.stack || '' }
  }

  componentDidCatch(error, errorInfo) {
    console.error('[ErrorBoundary]', error, errorInfo.componentStack)
  }

  handleReset = () => {
    const keys = [
      'nav_view',
      'nav_author_user_id',
      'nav_text_detail_id',
      'nav_market_card_id',
      'nav_msg_target_user_id',
    ]
    keys.forEach((k) => { try { localStorage.removeItem(k) } catch { /* noop */ } })
    location.reload()
  }

  render() {
    if (!this.state.error) return this.props.children

    const e = this.state.error
    return (
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        minHeight: '100dvh',
        padding: 24,
        background: '#f5f5f5',
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
      }}>
        <div style={{
          maxWidth: 420,
          width: '100%',
          background: '#fff',
          borderRadius: 12,
          padding: 32,
          boxShadow: '0 4px 24px rgba(0,0,0,0.1)',
        }}>
          <div style={{ fontSize: 28, marginBottom: 8, color: '#c00' }}>!</div>
          <h1 style={{ fontSize: 18, fontWeight: 600, margin: '0 0 4px', color: '#1a1a1a' }}>
            页面出错了
          </h1>
          <p style={{ fontSize: 14, color: '#666', margin: '0 0 16px' }}>
            应用遇到了一个意外错误，请尝试返回首页重新进入。
          </p>

          {import.meta.env.DEV && (
            <>
              <pre style={{
                fontSize: 12,
                color: '#c00',
                background: '#fef2f2',
                padding: 12,
                borderRadius: 8,
                overflow: 'auto',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-all',
                margin: '0 0 16px',
                maxHeight: 200,
              }}>
                {e.message || String(e)}
              </pre>

              {this.state.stack && (
                <details style={{ marginBottom: 16 }}>
                  <summary style={{ fontSize: 13, color: '#888', cursor: 'pointer' }}>
                    技术细节
                  </summary>
                  <pre style={{
                    fontSize: 11,
                    color: '#555',
                    background: '#f8f8f8',
                    padding: 8,
                    borderRadius: 6,
                    overflow: 'auto',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-all',
                    margin: '8px 0 0',
                    maxHeight: 300,
                  }}>
                    {this.state.stack}
                  </pre>
                </details>
              )}
            </>
          )}

          <button
            type="button"
            onClick={this.handleReset}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '10px 20px',
              border: 'none',
              borderRadius: 8,
              background: '#1677ff',
              color: '#fff',
              fontSize: 14,
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            返回首页
          </button>
        </div>
      </div>
    )
  }
}
