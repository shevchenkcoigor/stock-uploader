//! SFTP upload to Adobe Stock (port of `sftp_upload.py`), over `ssh2`.

use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::path::Path;
use std::time::Duration;

use ssh2::{Session, Sftp};

const NETWORK_TIMEOUT: Duration = Duration::from_secs(30);
const CHUNK_BYTES: usize = 64 * 1024;

/// Where and as whom to log in.
pub struct Login<'a> {
    pub host: &'a str,
    pub port: u16,
    pub user: &'a str,
    pub password: &'a str,
}

/// Puts one file on the server. A trait so the batch logic can be tested without a server.
pub trait Uploader {
    /// Sends `local` as `remote` (a name in the account root); `on_bytes` gets the total sent so far.
    fn put(&mut self, local: &Path, remote: &str, on_bytes: &mut dyn FnMut(u64)) -> Result<(), String>;
}

pub struct SftpUploader {
    sftp: Sftp,
    _session: Session,
}

/// Connects and logs in; the error text says which step failed.
pub fn connect(login: &Login) -> Result<SftpUploader, String> {
    let address = (login.host, login.port)
        .to_socket_addrs()
        .map_err(|error| format!("не удалось найти сервер: {error}"))?
        .next()
        .ok_or_else(|| "не удалось найти сервер".to_string())?;
    let tcp = TcpStream::connect_timeout(&address, NETWORK_TIMEOUT).map_err(|error| format!("нет соединения: {error}"))?;
    let mut session = Session::new().map_err(|error| format!("SSH: {error}"))?;
    session.set_timeout(u32::try_from(NETWORK_TIMEOUT.as_millis()).unwrap_or(30_000));
    session.set_tcp_stream(tcp);
    session.handshake().map_err(|error| format!("SSH рукопожатие: {error}"))?;
    session.userauth_password(login.user, login.password).map_err(|error| format!("вход не выполнен: {error}"))?;
    let sftp = session.sftp().map_err(|error| format!("не удалось открыть SFTP-сессию: {error}"))?;
    Ok(SftpUploader { sftp, _session: session })
}

impl Uploader for SftpUploader {
    fn put(&mut self, local: &Path, remote: &str, on_bytes: &mut dyn FnMut(u64)) -> Result<(), String> {
        let mut source = std::fs::File::open(local).map_err(|error| error.to_string())?;
        let mut target = self.sftp.create(Path::new(remote)).map_err(|error| error.to_string())?;
        let mut buffer = vec![0_u8; CHUNK_BYTES];
        let mut sent = 0_u64;
        loop {
            let read = source.read(&mut buffer).map_err(|error| error.to_string())?;
            if read == 0 {
                break;
            }
            target.write_all(&buffer[..read]).map_err(|error| error.to_string())?;
            sent += read as u64;
            on_bytes(sent);
        }
        target.flush().map_err(|error| error.to_string())
    }
}
