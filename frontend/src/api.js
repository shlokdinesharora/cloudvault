// Thin client: Cognito (direct REST, no SDK) + CloudVault HTTP API.
let cfg = null
export async function loadConfig() {
  if (!cfg) {
    const r = await fetch('/config.json')
    if (!r.ok) throw new Error('config.json missing - run scripts/make-config.sh')
    cfg = await r.json()
  }
  return cfg
}

async function cognito(target, body) {
  const c = await loadConfig()
  const r = await fetch(`https://cognito-idp.${c.region}.amazonaws.com/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-amz-json-1.1',
      'X-Amz-Target': `AWSCognitoIdentityProviderService.${target}`,
    },
    body: JSON.stringify({ ClientId: c.clientId, ...body }),
  })
  const j = await r.json()
  if (!r.ok) throw new Error(j.message || j.__type || 'Authentication error')
  return j
}

const KEY = 'cv_session'
const save = (s) => sessionStorage.setItem(KEY, JSON.stringify(s))
export const session = () => JSON.parse(sessionStorage.getItem(KEY) || 'null')
export const logout = () => sessionStorage.removeItem(KEY)

function parseJwt(t) {
  return JSON.parse(atob(t.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')))
}

export const signUp = (email, password) =>
  cognito('SignUp', { Username: email, Password: password, UserAttributes: [{ Name: 'email', Value: email }] })
export const confirmSignUp = (email, code) => cognito('ConfirmSignUp', { Username: email, ConfirmationCode: code })
export const resendCode = (email) => cognito('ResendConfirmationCode', { Username: email })

export async function login(email, password) {
  const r = await cognito('InitiateAuth', {
    AuthFlow: 'USER_PASSWORD_AUTH',
    AuthParameters: { USERNAME: email, PASSWORD: password },
  })
  const a = r.AuthenticationResult
  const s = { idToken: a.IdToken, refreshToken: a.RefreshToken, exp: parseJwt(a.IdToken).exp, email }
  save(s)
  return s
}

async function token() {
  let s = session()
  if (!s) throw new Error('Not signed in')
  if (s.exp - 60 < Date.now() / 1000) {
    const r = await cognito('InitiateAuth', { AuthFlow: 'REFRESH_TOKEN_AUTH', AuthParameters: { REFRESH_TOKEN: s.refreshToken } })
    const a = r.AuthenticationResult
    s = { ...s, idToken: a.IdToken, exp: parseJwt(a.IdToken).exp }
    save(s)
  }
  return s.idToken
}

async function call(method, path, body, auth = true) {
  const c = await loadConfig()
  const headers = { 'Content-Type': 'application/json' }
  if (auth) headers.Authorization = await token()
  const r = await fetch(c.apiUrl + path, { method, headers, body: body ? JSON.stringify(body) : undefined })
  const j = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(j.message || j.error || `Request failed (${r.status})`)
  return j
}

export const listFiles = (q = '') => call('GET', `/files${q ? `?q=${encodeURIComponent(q)}` : ''}`)
export const getFile = (id) => call('GET', `/files/${id}`)
export const deleteFile = (id) => call('DELETE', `/files/${id}`)
export const downloadUrl = (id, variant = 'original') => call('GET', `/files/${id}/download?variant=${variant}`)
export const createShare = (id, opts) => call('POST', `/files/${id}/share`, opts)
export const revokeShare = (token) => call('DELETE', `/shares/${token}`)
export const shareInfo = (token) => call('GET', `/s/${token}`, null, false)
export const shareDownload = (token, password) => call('POST', `/s/${token}/download`, { password }, false)

export async function uploadFile(file, onProgress) {
  const { upload } = await call('POST', '/files/upload-url', {
    filename: file.name,
    size: file.size,
    content_type: file.type || 'application/octet-stream',
  })
  await new Promise((resolve, reject) => {
    const form = new FormData()
    Object.entries(upload.fields).forEach(([k, v]) => form.append(k, v))
    form.append('file', file) // must be last
    const xhr = new XMLHttpRequest()
    xhr.open('POST', upload.url)
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(Math.round((e.loaded / e.total) * 100))
    xhr.onload = () => (xhr.status < 300 ? resolve() : reject(new Error('Upload rejected by storage')))
    xhr.onerror = () => reject(new Error('Network error during upload'))
    xhr.send(form)
  })
}
