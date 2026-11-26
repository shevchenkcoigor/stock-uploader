//! The SFTP password in the OS keychain, under the same service/account as the PySide app.
//! Test builds (the `e2e` feature) never open the keychain: they take the password from the environment.

use keyring::Entry;

use crate::error::{AppError, AppResult};

const SERVICE: &str = "stock-uploader";
/// The previous app name's entry: read when the new one is empty, never deleted.
const OLD_SERVICE: &str = "adobe-stock-uploader";
const ACCOUNT: &str = "sftp";
const E2E_PASSWORD_ENV: &str = "STOCK_E2E_SFTP_PASSWORD";

fn entry(service: &str) -> AppResult<Entry> {
    Ok(Entry::new(service, ACCOUNT)?)
}

fn read(service: &str) -> AppResult<Option<String>> {
    match entry(service)?.get_password() {
        Ok(password) => Ok(Some(password)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(error) => Err(error.into()),
    }
}

/// The current password, or else the old one, which is then saved under the new name.
fn carried_over(
    current: AppResult<Option<String>>,
    old: impl FnOnce() -> AppResult<Option<String>>,
    save: impl FnOnce(&str) -> AppResult<()>,
) -> AppResult<String> {
    if let Some(password) = current? {
        return Ok(password);
    }
    let Some(password) = old()? else { return Ok(String::new()) };
    // the password is usable even if saving it fails; the next read tries again
    if let Err(error) = save(&password) {
        eprintln!("could not save the password under the new keychain name: {error}");
    }
    Ok(password)
}

/// Reading may make macOS ask for the Mac password, so call it only when the password is needed.
pub fn get() -> AppResult<String> {
    if cfg!(feature = "e2e") {
        return Ok(std::env::var(E2E_PASSWORD_ENV).unwrap_or_default());
    }
    carried_over(read(SERVICE), || read(OLD_SERVICE), set)
}

pub fn set(password: &str) -> AppResult<()> {
    if cfg!(feature = "e2e") {
        return Err(AppError::Check("a test build does not save passwords".into()));
    }
    entry(SERVICE)?.set_password(password)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::cell::RefCell;

    use super::*;

    fn run(current: Option<&str>, old: Option<&str>) -> (String, Vec<String>) {
        let saved = RefCell::new(Vec::new());
        let password = carried_over(
            Ok(current.map(String::from)),
            || Ok(old.map(String::from)),
            |value| {
                saved.borrow_mut().push(value.to_string());
                Ok(())
            },
        )
        .unwrap();
        (password, saved.into_inner())
    }

    #[test]
    fn the_old_password_is_carried_over_only_when_there_is_no_new_one() {
        assert_eq!(run(Some("new"), Some("old")), ("new".into(), vec![]));
        assert_eq!(run(None, Some("old")), ("old".into(), vec!["old".to_string()]));
        assert_eq!(run(None, None), (String::new(), vec![]));
    }

    #[test]
    fn a_failed_save_still_returns_the_password() {
        let password = carried_over(Ok(None), || Ok(Some("old".into())), |_| Err(AppError::Check("locked".into())));

        assert_eq!(password.unwrap(), "old");
    }
}
