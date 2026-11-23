import { spawnSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { existsSync } from 'node:fs'
import { createInterface } from 'node:readline'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import makeWASocket, {
  Browsers,
  BufferJSON,
  DisconnectReason,
  initAuthCreds,
} from '@whiskeysockets/baileys'

const require = createRequire(import.meta.url)
const QRCode = require('qrcode-terminal/vendor/QRCode')
const QRErrorCorrectLevel = require('qrcode-terminal/vendor/QRCode/QRErrorCorrectLevel')
const here = dirname(fileURLToPath(import.meta.url))
const repository = resolve(here, '..', '..')
const bridge = join(here, 'dpapi_store.py')
const bundledPython = join(repository, 'backend', '.venv', 'Scripts', 'python.exe')
const python = process.env.EDISK_PYTHON || bundledPython
const capability = process.env.EDISK_WHATSAPP_IPC_TOKEN || ''
const accountId = process.env.EDISK_WHATSAPP_ACCOUNT_ID || ''

if (
  process.platform !== 'win32' ||
  !existsSync(python) ||
  !/^[0-9a-f]{64}$/.test(capability) ||
  !/^[0-9a-f]{8}-[0-9a-f]{4}-[4][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(accountId)
) {
  throw new Error('Contexto privado do WhatsApp indisponivel.')
}

const silentLogger = {
  level: 'silent',
  child() { return this },
  trace() {},
  debug() {},
  info() {},
  warn() {},
  error() {},
  fatal() {},
}

function bridgeCall(command, input) {
  const result = spawnSync(python, ['-B', bridge, command], {
    cwd: here,
    input,
    maxBuffer: 20 * 1024 * 1024,
    windowsHide: true,
  })
  if (result.status !== 0) throw new Error('Armazenamento protegido indisponivel.')
  return result.stdout
}

function newState() {
  return { creds: initAuthCreds(), keys: {} }
}

function readState() {
  const raw = bridgeCall('read')
  if (!raw.length) return newState()
  try {
    const parsed = JSON.parse(raw.toString('utf8'), BufferJSON.reviver)
    if (!parsed?.creds || typeof parsed.keys !== 'object') throw new Error()
    return parsed
  } catch {
    throw new Error('Sessao protegida invalida.')
  }
}

let state = readState()
let writeTail = Promise.resolve()
let socket = null
let qrMatrix = null
let connectionState = state.creds.registered ? 'desconectado' : 'pareamento_necessario'
let manualDisconnect = false
let reconnectAttempts = 0
let reconnectTimer = null
let unlinkTask = null

function persist() {
  writeTail = writeTail.then(() => {
    const raw = Buffer.from(JSON.stringify(state, BufferJSON.replacer), 'utf8')
    bridgeCall('write', raw)
  })
  return writeTail
}

const auth = {
  get creds() { return state.creds },
  keys: {
    async get(type, ids) {
      const values = state.keys[type] || {}
      return Object.fromEntries(ids.filter((id) => values[id] !== undefined).map((id) => [id, values[id]]))
    },
    async set(data) {
      for (const [type, values] of Object.entries(data)) {
        state.keys[type] ||= {}
        for (const [id, value] of Object.entries(values)) {
          if (value === null || value === undefined) delete state.keys[type][id]
          else state.keys[type][id] = value
        }
      }
      await persist()
    },
  },
}

function makeQrMatrix(value) {
  const code = new QRCode(-1, QRErrorCorrectLevel.L)
  code.addData(value)
  code.make()
  return code.modules.map((row) => row.map((cell) => Boolean(cell)))
}

function statusLine(requestId = null, extra = {}) {
  return `${JSON.stringify({
    capacidade: capability,
    evento: 'status',
    requisicao_id: requestId,
    estado: connectionState,
    sessao_persistida: Boolean(state.creds.registered),
    qr: qrMatrix,
    ...extra,
  })}\n`
}

function emitStatus(requestId = null, extra = {}) {
  process.stdout.write(statusLine(requestId, extra))
}

function closeSocket() {
  const current = socket
  socket = null
  if (current) current.end(new Error('desconexao_solicitada'))
}

function scheduleReconnect() {
  if (manualDisconnect || unlinkTask || reconnectAttempts >= 3 || reconnectTimer) return
  const delay = 2 ** reconnectAttempts * 1000
  reconnectAttempts += 1
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    connect(true).catch(() => {
      connectionState = 'erro_conexao'
      emitStatus()
    })
  }, delay)
}

async function connect(reconnecting = false) {
  if (socket || ['conectando', 'qr_disponivel', 'conectado'].includes(connectionState)) return
  manualDisconnect = false
  qrMatrix = null
  connectionState = reconnecting ? 'reconectando' : 'conectando'
  emitStatus()
  const current = makeWASocket({
    auth,
    logger: silentLogger,
    browser: Browsers.windows('eDisk'),
    markOnlineOnConnect: false,
    syncFullHistory: false,
    shouldSyncHistoryMessage: () => false,
    printQRInTerminal: false,
    connectTimeoutMs: 30_000,
    qrTimeout: 60_000,
  })
  socket = current
  current.ev.on('creds.update', async (update) => {
    if (socket !== current) return
    Object.assign(state.creds, update)
    try {
      await persist()
    } catch {
      connectionState = 'erro_armazenamento'
      manualDisconnect = true
      qrMatrix = null
      closeSocket()
      emitStatus()
    }
  })
  current.ev.on('connection.update', async ({ connection, lastDisconnect, qr }) => {
    if (socket !== current) return
    if (qr) {
      qrMatrix = makeQrMatrix(qr)
      connectionState = 'qr_disponivel'
      emitStatus()
    }
    if (connection === 'open') {
      try {
        await writeTail
      } catch {
        connectionState = 'erro_armazenamento'
        manualDisconnect = true
        qrMatrix = null
        closeSocket()
        emitStatus()
        return
      }
      if (socket !== current) return
      qrMatrix = null
      connectionState = unlinkTask ? 'desvinculando' : 'conectado'
      reconnectAttempts = 0
      emitStatus()
    }
    if (connection === 'close') {
      if (socket === current) socket = null
      qrMatrix = null
      const statusCode = lastDisconnect?.error?.output?.statusCode
      if (manualDisconnect) {
        connectionState = state.creds.registered ? 'desconectado' : 'pareamento_necessario'
      } else if (statusCode === DisconnectReason.loggedOut) {
        connectionState = 'sessao_invalida'
      } else if (unlinkTask) {
        connectionState = 'erro_desvinculo'
      } else {
        connectionState = 'desconectado'
        scheduleReconnect()
      }
      emitStatus({ codigo_desconexao: Number.isInteger(statusCode) ? statusCode : null })
    }
  })
}

async function unlink() {
  if (reconnectTimer) clearTimeout(reconnectTimer)
  reconnectTimer = null
  qrMatrix = null
  if (state.creds.registered) {
    if (!socket) await connect()
    const current = socket
    if (!current) throw new Error('sessao_indisponivel')
    const connected = current.ws.isOpen
    connectionState = 'desvinculando'
    emitStatus()
    if (!connected) {
      try {
        await current.waitForConnectionUpdate(
          async ({ connection }) => connection === 'open',
          30_000,
        )
      } catch (error) {
        if (connectionState !== 'sessao_invalida') throw error
      }
    }
    if (connectionState !== 'sessao_invalida') {
      manualDisconnect = true
      try {
        await current.logout()
      } catch (error) {
        manualDisconnect = false
        throw error
      }
      if (socket === current) socket = null
    }
  } else {
    manualDisconnect = true
    closeSocket()
  }
  await writeTail.catch(() => {})
  bridgeCall('clear')
  state = newState()
  connectionState = 'pareamento_necessario'
  reconnectAttempts = 0
}

function startUnlink() {
  if (unlinkTask) return
  connectionState = 'desvinculando'
  qrMatrix = null
  emitStatus()
  unlinkTask = unlink()
    .then(() => emitStatus())
    .catch(() => {
      connectionState = 'erro_desvinculo'
      emitStatus()
    })
    .finally(() => { unlinkTask = null })
}

async function sendText(request) {
  if (
    !/^\+[1-9][0-9]{1,14}$/.test(request.destino_e164 || '') ||
    typeof request.texto !== 'string' ||
    !request.texto.trim() ||
    Buffer.byteLength(request.texto, 'utf8') > 65_536 ||
    !/^[0-9a-f-]{36}$/.test(request.tentativa_id || '')
  ) {
    return { estado: 'falhou', codigo: 'entrada_invalida', message_id: null }
  }
  const current = socket
  if (
    connectionState !== 'conectado' ||
    !state.creds.registered ||
    !current ||
    !current.ws.isOpen
  ) {
    return { estado: 'falhou', codigo: 'conta_desconectada', message_id: null }
  }
  try {
    const sent = await current.sendMessage(
      `${request.destino_e164.slice(1)}@s.whatsapp.net`,
      { text: request.texto },
    )
    const messageId = sent?.key?.id
    if (typeof messageId !== 'string' || !messageId || messageId.length > 256) {
      return { estado: 'desconhecida', codigo: 'sem_confirmacao', message_id: null }
    }
    return { estado: 'confirmada', codigo: 'aceita', message_id: messageId }
  } catch {
    return { estado: 'desconhecida', codigo: 'sem_confirmacao', message_id: null }
  }
}

async function shutdown(requestId) {
  manualDisconnect = true
  if (reconnectTimer) clearTimeout(reconnectTimer)
  qrMatrix = null
  closeSocket()
  await writeTail
  connectionState = state.creds.registered ? 'desconectado' : 'pareamento_necessario'
  await new Promise((resolve) => process.stdout.write(statusLine(requestId), resolve))
  process.exit(0)
}

emitStatus()
const input = createInterface({ input: process.stdin, terminal: false })
input.on('line', async (line) => {
  let request
  try {
    request = JSON.parse(line)
  } catch {
    return
  }
  if (
    request?.capacidade !== capability ||
    typeof request?.requisicao_id !== 'string' ||
    request.requisicao_id.length > 64
  ) return
  const command = request.comando
  try {
    if (command === 'connect') await connect()
    else if (command === 'disconnect') {
      manualDisconnect = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      reconnectTimer = null
      qrMatrix = null
      closeSocket()
      connectionState = state.creds.registered ? 'desconectado' : 'pareamento_necessario'
    } else if (command === 'unlink') {
      startUnlink()
      emitStatus(request.requisicao_id)
      return
    } else if (command === 'send_text') {
      const resultado = await sendText(request)
      emitStatus(request.requisicao_id, { resultado_envio: resultado })
      return
    }
    else if (command === 'exit') {
      await shutdown(request.requisicao_id)
      return
    } else if (command !== 'status') {
      emitStatus(request.requisicao_id, { erro: 'comando_invalido' })
      return
    }
    emitStatus(request.requisicao_id)
  } catch {
    qrMatrix = null
    connectionState = 'erro'
    emitStatus(request.requisicao_id, { erro: 'operacao_indisponivel' })
  }
})
process.on('SIGINT', () => { shutdown(null).catch(() => process.exit(1)) })
process.on('SIGTERM', () => { shutdown(null).catch(() => process.exit(1)) })
