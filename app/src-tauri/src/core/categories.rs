//! Adobe Stock content categories: numeric code -> name (copied from Adobe's help, 2026-11-20).

pub const CATEGORIES: [(u32, &str); 21] = [
    (1, "Animals"),
    (2, "Buildings and Architecture"),
    (3, "Business"),
    (4, "Drinks"),
    (5, "The Environment"),
    (6, "States of Mind"),
    (7, "Food"),
    (8, "Graphic Resources"),
    (9, "Hobbies and Leisure"),
    (10, "Industry"),
    (11, "Landscapes"),
    (12, "Lifestyle"),
    (13, "People"),
    (14, "Plants and Flowers"),
    (15, "Culture and Religion"),
    (16, "Science"),
    (17, "Social Issues"),
    (18, "Sports"),
    (19, "Technology"),
    (20, "Transport"),
    (21, "Travel"),
];

pub fn is_category_code(code: i64) -> bool {
    CATEGORIES.iter().any(|(known, _)| i64::from(*known) == code)
}

/// "1 Animals, 2 Buildings and Architecture, ..." for use in prompts.
pub fn category_menu() -> String {
    CATEGORIES.iter().map(|(code, name)| format!("{code} {name}")).collect::<Vec<_>>().join(", ")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn menu_lists_every_category_in_order() {
        let menu = category_menu();
        assert!(menu.starts_with("1 Animals, 2 Buildings and Architecture, 3 Business"));
        assert!(menu.ends_with("20 Transport, 21 Travel"));
        assert!(is_category_code(21) && !is_category_code(22) && !is_category_code(0));
    }
}
