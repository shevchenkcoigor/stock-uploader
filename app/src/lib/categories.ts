/** Adobe Stock content categories: numeric code -> name (adobe_stock/categories.py). */
export const CATEGORIES: Readonly<Record<number, string>> = {
  1: 'Animals',
  2: 'Buildings and Architecture',
  3: 'Business',
  4: 'Drinks',
  5: 'The Environment',
  6: 'States of Mind',
  7: 'Food',
  8: 'Graphic Resources',
  9: 'Hobbies and Leisure',
  10: 'Industry',
  11: 'Landscapes',
  12: 'Lifestyle',
  13: 'People',
  14: 'Plants and Flowers',
  15: 'Culture and Religion',
  16: 'Science',
  17: 'Social Issues',
  18: 'Sports',
  19: 'Technology',
  20: 'Transport',
  21: 'Travel',
}

export const CATEGORY_OPTIONS = Object.entries(CATEGORIES).map(([code, name]) => ({ code, name }))

/** The category's name for a stored code; empty when none is set (or the code is unknown). */
export const categoryName = (code: string): string => (/^\d+$/.test(code) ? (CATEGORIES[Number(code)] ?? '') : '')
