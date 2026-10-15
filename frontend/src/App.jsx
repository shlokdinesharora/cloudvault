import { useCallback, useEffect, useRef, useState } from 'react'
import * as api from './api.js'

const FINAL = ['DONE', 'FAILED', 'QUARANTINED', 'BLOCKED']
const fmtSize = (n) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`)

function go(url) {
  window.location.href = url
}

/* ------------------------------------------------------------- public share page */
function SharePage({ token }) {
  const [info, setInfo] = useState(null)
  const [error, setError] = useState('')
  const [password, setPassword] = useState('')
  useEffect(() => {
    api.shareInfo(token).then(setInfo).catch((e) => setError(e.message))
  }, [token])
  const download = async () => {
    setError('')
    try {
      go((await api.shareDownload(token, password)).url)
    } catch (e) {
      setError(e.message)
    }
  }
  return (
    <div className="center">
      <div className="card narrow">
        <h1>☁ CloudVault</h1>
        {info ? (
          <>
            <h2>{info.filename}</h2>
            <p className="muted">{fmtSize(info.size)} · link expires {new Date(info.expires_at * 1000).toLocaleString()}</p>
            {info.password_required && (
              <input type="password" placeholder="Password" value={password} onChange={(e) => setPassword(e.target.value)} />
            )}
            <button onClick={download}>Download</button>
          </>
        ) : (
          !error && <p>Loading…</p>
        )}
        {error && <p className="error">{error}</p>}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------- auth */
function Auth({ onLogin }) {
  const [mode, setMode] = useState('login') // login | signup | confirm
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const run = async (fn) => {
    setBusy(true)
    setMsg('')
    try {
      await fn()
    } catch (e) {
      setMsg(e.message)
    }
    setBusy(false)
  }
  const submit = (e) => {
    e.preventDefault()
    run(async () => {
      if (mode === 'signup') {
        await api.signUp(email, password)
        setMode('confirm')
        setMsg('We emailed you a verification code.')
      } else if (mode === 'confirm') {
        await api.confirmSignUp(email, code)
        await onLogin(email, password)
      } else {
        try {
          await onLogin(email, password)
        } catch (err) {
          if (/not confirmed/i.test(err.message)) {
            await api.resendCode(email)
            setMode('confirm')
            setMsg('Account not verified yet. We sent a new code.')
          } else throw err
        }
      }
    })
  }
  return (
    <div className="center">
      <form className="card narrow" onSubmit={submit}>
        <h1>☁ CloudVault</h1>
        <p className="muted">Secure file processing & sharing</p>
        <input type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        {mode !== 'confirm' && (
          <input type="password" placeholder="Password (8+ chars, upper, lower, number)" value={password} onChange={(e) => setPassword(e.target.value)} required />
        )}
        {mode === 'confirm' && <input placeholder="Verification code" value={code} onChange={(e) => setCode(e.target.value)} required />}
        <button disabled={busy}>{mode === 'login' ? 'Sign in' : mode === 'signup' ? 'Create account' : 'Verify & sign in'}</button>
        {msg && <p className={mode === 'confirm' && !/not|wrong|invalid/i.test(msg) ? 'muted' : 'error'}>{msg}</p>}
        <p className="muted small">
          {mode === 'login' ? (
            <a href="#" onClick={(e) => (e.preventDefault(), setMode('signup'))}>Need an account? Sign up</a>
          ) : (
            <a href="#" onClick={(e) => (e.preventDefault(), setMode('login'))}>Back to sign in</a>
          )}
        </p>
      </form>
    </div>
  )
}

/* ------------------------------------------------------------- share dialog */
function ShareDialog({ file, onClose }) {
  const [hours, setHours] = useState(24)
  const [max, setMax] = useState('')
  const [password, setPassword] = useState('')
  const [shares, setShares] = useState([])
  const [err, setErr] = useState('')
  const refresh = useCallback(() => api.getFile(file.file_id).then((r) => setShares(r.shares)).catch((e) => setErr(e.message)), [file.file_id])
  useEffect(() => { refresh() }, [refresh])
  const link = (t) => `${window.location.origin}/s/${t}`
  const create = async () => {
    setErr('')
    try {
      await api.createShare(file.file_id, { expires_in_hours: +hours, max_downloads: max ? +max : null, password })
      setPassword('')
      refresh()
    } catch (e) { setErr(e.message) }
  }
  return (
    <div className="overlay" onClick={onClose}>
      <div className="card modal" onClick={(e) => e.stopPropagation()}>
        <h2>Share “{file.filename}”</h2>
        <div className="row">
          <label>Expires in
            <select value={hours} onChange={(e) => setHours(e.target.value)}>
              <option value={1}>1 hour</option><option value={24}>24 hours</option><option value={72}>3 days</option><option value={168}>7 days</option>
            </select>
          </label>
          <label>Max downloads
            <input type="number" min="1" placeholder="unlimited" value={max} onChange={(e) => setMax(e.target.value)} />
          </label>
        </div>
        <input type="password" placeholder="Optional password" value={password} onChange={(e) => setPassword(e.target.value)} />
        <button onClick={create}>Create link</button>
        {err && <p className="error">{err}</p>}
        <h3>Active links</h3>
        {shares.length === 0 && <p className="muted">None yet.</p>}
        {shares.map((s) => (
          <div className="share" key={s.token}>
            <input readOnly value={link(s.token)} onFocus={(e) => e.target.select()} />
            <span className="muted small">
              {s.password_protected ? '🔒 ' : ''}{s.downloads}{s.max_downloads ? `/${s.max_downloads}` : ''} downloads · expires {new Date(s.expires_at * 1000).toLocaleString()}
            </span>
            <button className="ghost" onClick={() => navigator.clipboard.writeText(link(s.token))}>Copy</button>
            <button className="danger" onClick={() => api.revokeShare(s.token).then(refresh)}>Revoke</button>
          </div>
        ))}
        <button className="ghost" onClick={onClose}>Close</button>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------- dashboard */
function Dashboard({ user, onLogout }) {
  const [files, setFiles] = useState([])
  const [q, setQ] = useState('')
  const [uploads, setUploads] = useState({}) // name -> pct
  const [sharing, setSharing] = useState(null)
  const [error, setError] = useState('')
  const input = useRef()

  const refresh = useCallback(() => api.listFiles(q).then((r) => setFiles(r.files)).catch((e) => setError(e.message)), [q])
  useEffect(() => { refresh() }, [refresh])
  useEffect(() => {
    if (!files.some((f) => !FINAL.includes(f.status))) return
    const t = setInterval(refresh, 3000)
    return () => clearInterval(t)
  }, [files, refresh])

  const upload = async (list) => {
    setError('')
    for (const f of list) {
      setUploads((u) => ({ ...u, [f.name]: 0 }))
      try {
        await api.uploadFile(f, (p) => setUploads((u) => ({ ...u, [f.name]: p })))
      } catch (e) { setError(`${f.name}: ${e.message}`) }
      setUploads((u) => { const n = { ...u }; delete n[f.name]; return n })
      refresh()
    }
  }
  const download = async (id, variant) => {
    try { go((await api.downloadUrl(id, variant)).url) } catch (e) { setError(e.message) }
  }
  const remove = async (f) => {
    if (confirm(`Delete ${f.filename} and all its share links?`)) { await api.deleteFile(f.file_id); refresh() }
  }

  return (
    <div className="page">
      <header>
        <h1>☁ CloudVault</h1>
        <span className="muted">{user.email} <button className="ghost" onClick={onLogout}>Sign out</button></span>
      </header>

      <div className="drop card" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); upload([...e.dataTransfer.files]) }} onClick={() => input.current.click()}>
        <input ref={input} type="file" multiple hidden onChange={(e) => upload([...e.target.files])} />
        <p><b>Drop files here</b> or click to browse (max 100 MB each)</p>
        {Object.entries(uploads).map(([n, p]) => (
          <div key={n} className="bar"><span>{n}</span><div><i style={{ width: `${p}%` }} /></div><span>{p}%</span></div>
        ))}
      </div>

      <input className="search" placeholder="Search by file name or text content…" value={q} onChange={(e) => setQ(e.target.value)} />
      {error && <p className="error">{error}</p>}

      <div className="card">
        <table>
          <thead><tr><th>Name</th><th>Size</th><th>Status</th><th>Details</th><th></th></tr></thead>
          <tbody>
            {files.length === 0 && <tr><td colSpan="5" className="muted">No files yet.</td></tr>}
            {files.map((f) => (
              <tr key={f.file_id}>
                <td>{f.filename}</td>
                <td>{fmtSize(f.size)}</td>
                <td><span className={`badge ${f.status}`}>{f.status.replace('_', ' ')}</span></td>
                <td className="small muted">
                  {f.status === 'DONE' && <>sha256 {f.sha256.slice(0, 10)}… · {f.detected_type} · gz {fmtSize(f.compressed_size)}</>}
                  {f.warnings?.map((w) => <div key={w} className="warn">⚠ {w}</div>)}
                  {f.error && <div className="error">{f.error}</div>}
                </td>
                <td className="actions">
                  {f.status === 'DONE' && <>
                    <button className="ghost" onClick={() => download(f.file_id, 'original')}>Download</button>
                    <button className="ghost" onClick={() => download(f.file_id, 'compressed')}>.gz</button>
                    <button onClick={() => setSharing(f)}>Share</button>
                  </>}
                  <button className="danger" onClick={() => remove(f)}>Delete</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {sharing && <ShareDialog file={sharing} onClose={() => setSharing(null)} />}
    </div>
  )
}

/* ------------------------------------------------------------- root */
export default function App() {
  const [user, setUser] = useState(api.session())
  const m = window.location.pathname.match(/^\/s\/([\w-]+)/)
  if (m) return <SharePage token={m[1]} />
  if (!user) return <Auth onLogin={async (e, p) => setUser(await api.login(e, p))} />
  return <Dashboard user={user} onLogout={() => { api.logout(); setUser(null) }} />
}
