// Drive open-design /api/chat with an SSE client.
// Usage: node e2e/od-chat.cjs <message-file> [projectId]
const fs = require('fs')

const PORT = process.env.OD_PORT || '59904'
const projectId = process.argv[3] || 'char-distill-redesign'
const message = fs.readFileSync(process.argv[2], 'utf8')

;(async () => {
  const res = await fetch(`http://127.0.0.1:${PORT}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      agentId: 'claude',
      model: 'default',
      projectId,
      message,
      mediaExecution: { policy: 'deny' },
    }),
  })
  console.log('STATUS', res.status)
  if (!res.ok) {
    const t = await res.text()
    console.error(t.slice(0, 2000))
    process.exit(1)
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  let done = false
  const onEvent = (event, data) => {
    console.log(`[${event}]`, data ? JSON.stringify(data).slice(0, 400) : '')
  }
  while (!done) {
    const { value, done: d } = await reader.read()
    done = d
    if (value) buf += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buf.indexOf('\n\n')) !== -1) {
      const frame = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      let event = 'message'
      let dataLine = null
      for (const line of frame.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) dataLine = line.slice(5).replace(/^ /, '')
      }
      let parsed = null
      if (dataLine) {
        try { parsed = JSON.parse(dataLine) } catch { parsed = dataLine }
      }
      onEvent(event, parsed)
    }
  }
  console.log('STREAM_END')
})().catch((e) => { console.error('ERR', e.message); process.exit(1) })
