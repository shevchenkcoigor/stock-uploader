// Stands in for Adobe's SFTP server: listens on 127.0.0.1 only, takes the one test login and keeps what is
// uploaded in memory. It can hold uploads back, so a test sees the window while a run is in progress.
import { generateKeyPairSync } from 'node:crypto'
import type { AddressInfo } from 'node:net'
import ssh2, { type Connection, type SFTPWrapper } from 'ssh2'
import { sftpLogin } from './environment.ts'

const { Server, utils } = ssh2
const { STATUS_CODE } = utils.sftp
const KEY_BITS = 2048

export interface SftpServer {
  port: number
  /** What arrived, by remote name. */
  files: Map<string, Buffer>
  /** The remote name of every upload that was started, in order. */
  opened: string[]
  /** Uploads started from now on wait until `release`. */
  hold(): void
  release(): void
  close(): void
}

export async function startSftpServer(): Promise<SftpServer> {
  const files = new Map<string, Buffer>()
  const opened: string[] = []
  const clients = new Set<Connection>()
  let held: Array<() => void> | null = null

  const serve = (sftp: SFTPWrapper) => {
    const names = new Map<string, string>()
    let handles = 0
    sftp.on('OPEN', (id, name) => {
      opened.push(name)
      const handle = Buffer.from(String((handles += 1)))
      const accept = () => {
        names.set(handle.toString(), name)
        files.set(name, Buffer.alloc(0))
        sftp.handle(id, handle)
      }
      if (held) held.push(accept)
      else accept()
    })
    sftp.on('WRITE', (id, handle, offset, data) => {
      const name = names.get(handle.toString())
      if (name === undefined) return sftp.status(id, STATUS_CODE.FAILURE)
      const before = files.get(name) ?? Buffer.alloc(0)
      const after = Buffer.alloc(Math.max(before.length, offset + data.length))
      before.copy(after)
      data.copy(after, offset)
      files.set(name, after)
      sftp.status(id, STATUS_CODE.OK)
    })
    sftp.on('CLOSE', (id) => sftp.status(id, STATUS_CODE.OK))
    // the client waits for the channel to be closed from this side too before it ends the session
    sftp.on('end', () => sftp.end())
  }

  // a new host key every run: the app does not remember server keys
  const hostKey = generateKeyPairSync('rsa', {
    modulusLength: KEY_BITS,
    publicKeyEncoding: { type: 'pkcs1', format: 'pem' },
    privateKeyEncoding: { type: 'pkcs1', format: 'pem' },
  }).privateKey

  // one key exchange that libssh2 can do with every crypto backend: the Windows build uses the system's
  const config = { hostKeys: [hostKey], algorithms: { kex: ['diffie-hellman-group14-sha256' as const] } }
  const server = new Server(config, (client) => {
    clients.add(client)
    client.on('close', () => clients.delete(client))
    // the app drops the connection without a goodbye; that is not a test failure
    client.on('error', () => undefined)
    client.on('authentication', (context) => {
      const known = context.method === 'password' && context.username === sftpLogin.user
      if (known && context.password === sftpLogin.password) context.accept()
      else context.reject(['password'])
    })
    client.on('ready', () => {
      client.on('session', (acceptSession) => {
        acceptSession().on('sftp', (acceptSftp) => serve(acceptSftp()))
      })
    })
  })

  await new Promise<void>((done) => server.listen(0, '127.0.0.1', done))

  return {
    port: (server.address() as AddressInfo).port,
    files,
    opened,
    hold: () => {
      held = []
    },
    release: () => {
      const waiting = held ?? []
      held = null
      waiting.forEach((accept) => accept())
    },
    close: () => {
      clients.forEach((client) => client.end())
      server.close()
    },
  }
}
