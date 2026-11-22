//! The SFTP password in the OS keychain, under the same service/account as the PySide app.

use keyring::Entry;

use crate::error::AppResult;

const SERVICE: &str = "adobe-stock-uploader";
const ACCOUNT: &str = "sftp";

fn entry() -> AppResult<Entry> {
    Ok(Entry::new(SERVICE, ACCOUNT)?)
}

/// Reading may make macOS ask for the Mac password, so call it only when the password is needed.
pub fn get() -> AppResult<String> {
    match entry()?.get_password() {
        Ok(password) => Ok(password),
        Err(keyring::Error::NoEntry) => Ok(String::new()),
        Err(error) => Err(error.into()),
    }
}

pub fn set(password: &str) -> AppResult<()> {
    entry()?.set_password(password)?;
    Ok(())
}
