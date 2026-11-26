//! Environment variables renamed from `ADOBE_*` to `STOCK_*`; the old names are still honoured.

use std::ffi::OsString;

fn first_set(name: &str, legacy: &str, get: impl Fn(&str) -> Option<OsString>) -> Option<OsString> {
    get(name).filter(|value| !value.is_empty()).or_else(|| get(legacy).filter(|value| !value.is_empty()))
}

/// The value of `name`, or else of its old name; an empty value counts as unset.
pub fn var(name: &str, legacy: &str) -> Option<OsString> {
    first_set(name, legacy, |key| std::env::var_os(key))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lookup(set: &'static [(&str, &str)]) -> Option<OsString> {
        first_set("STOCK_X", "ADOBE_X", |key| set.iter().find(|(k, _)| *k == key).map(|(_, v)| OsString::from(v)))
    }

    #[test]
    fn the_new_name_wins_and_the_old_one_still_works() {
        assert_eq!(lookup(&[("STOCK_X", "new"), ("ADOBE_X", "old")]), Some("new".into()));
        assert_eq!(lookup(&[("ADOBE_X", "old")]), Some("old".into()));
        assert_eq!(lookup(&[("STOCK_X", ""), ("ADOBE_X", "old")]), Some("old".into()));
        assert_eq!(lookup(&[]), None);
    }
}
