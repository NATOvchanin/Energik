import unittest

from tools import parse_search_request


class SearchParserTests(unittest.TestCase):
    def test_google_news_keeps_topic(self):
        self.assertEqual(
            parse_search_request("Загугли свежие новости по искусственному интеллекту"),
            ("news", "новости искусственному интеллекту"),
        )

    def test_whats_new_keeps_company(self):
        self.assertEqual(
            parse_search_request("Что нового в OpenAI?"),
            ("news", "новости OpenAI"),
        )

    def test_news_question_keeps_topic(self):
        self.assertEqual(
            parse_search_request("Какие новости по разработке игр?"),
            ("news", "новости разработке игр"),
        )

    def test_find_price_without_where_phrase(self):
        self.assertEqual(
            parse_search_request("Найди цену книги «Первая научная история войны 1812 года»"),
            ("text", "цену книги «Первая научная история войны 1812 года»"),
        )

    def test_search_with_internet_phrase(self):
        self.assertEqual(
            parse_search_request("Поищи в интернете отзывы о Gemma 4"),
            ("text", "отзывы о Gemma 4"),
        )

    def test_who_is(self):
        self.assertEqual(
            parse_search_request("Кто такой Панасенков?"),
            ("text", "Панасенков"),
        )

    def test_no_search_command(self):
        self.assertIsNone(parse_search_request("Расскажи анекдот"))

    def test_empty_search_command(self):
        self.assertIsNone(parse_search_request("Загугли"))


if __name__ == "__main__":
    unittest.main()
