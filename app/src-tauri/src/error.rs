use serde::{Serialize, Serializer};

#[derive(Debug, thiserror::Error)]
pub enum AppError {
    #[error("база данных: {0}")]
    Db(#[from] rusqlite::Error),
    #[error("файл настроек: {0}")]
    Io(#[from] std::io::Error),
    #[error("файл настроек повреждён: {0}")]
    Json(#[from] serde_json::Error),
    #[error("{0}")]
    Invalid(String),
    #[error("связка ключей: {0}")]
    Keychain(#[from] keyring::Error),
    #[error("{0}")]
    Check(String),
    #[error("{0}")]
    StateDb(String),
}

impl Serialize for AppError {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(&self.to_string())
    }
}

pub type AppResult<T> = Result<T, AppError>;
